from __future__ import annotations

import io
import wave
from collections import deque
from datetime import datetime
from pathlib import Path
from threading import Condition
from typing import Callable, Iterator

import numpy as np
import sounddevice as sd

from voxd.core.vad_segmenter import VadSegmenter
from voxd.paths import DATA_DIR, RECORDINGS_DIR
from voxd.utils.libw import verbo, verr


_SegmentItem = tuple[int, bytes, int, bool]


class _LiveSegmentStream:
    """One ordered synchronization boundary for segment data and termination."""

    def __init__(self, capacity: int):
        self.capacity = capacity
        self._condition = Condition()
        self._items: deque[_SegmentItem] = deque()
        self._error: Exception | None = None
        self._closed = False

    @property
    def error(self) -> Exception | None:
        with self._condition:
            return self._error

    def offer(self, item: _SegmentItem) -> bool:
        with self._condition:
            if self._closed or self._error is not None:
                return False
            if len(self._items) >= self.capacity:
                unit = "segment" if self.capacity == 1 else "segments"
                self._error = RuntimeError(
                    f"transcription fell more than {self.capacity} {unit} behind"
                )
                self._items.clear()
                self._condition.notify_all()
                return False
            self._items.append(item)
            self._condition.notify()
            return True

    def close(self, final_item: _SegmentItem | None = None) -> bool:
        """Atomically enqueue final data before making closure observable."""
        with self._condition:
            if self._closed:
                return False

            accepted = self._error is None
            if accepted and final_item is not None:
                if len(self._items) >= self.capacity:
                    unit = "segment" if self.capacity == 1 else "segments"
                    self._error = RuntimeError(
                        f"transcription fell more than {self.capacity} {unit} behind"
                    )
                    self._items.clear()
                    accepted = False
                else:
                    self._items.append(final_item)

            self._closed = True
            self._condition.notify_all()
            return accepted

    def fail(self, error: Exception) -> None:
        with self._condition:
            if self._closed or self._error is not None:
                return
            self._error = error
            self._items.clear()
            self._condition.notify_all()

    def iter_items(self) -> Iterator[_SegmentItem]:
        while True:
            with self._condition:
                self._condition.wait_for(
                    lambda: bool(self._items)
                    or self._error is not None
                    or self._closed
                )
                if self._error is not None:
                    raise RuntimeError(
                        f"live audio segment stream failed: {self._error}"
                    ) from self._error
                if not self._items:
                    return
                item = self._items.popleft()
            yield item


class AudioRecorder:
    """Stream microphone PCM to bounded temporary WAV chunks."""

    def __init__(
        self,
        *,
        samplerate: int = 16000,
        channels: int = 1,
        chunk_seconds: int = 300,
        input_device: str = "",
        prefer_pulse: bool = True,
        segment_seconds: float | None = None,
        segment_queue_size: int = 8,
        segmenter_factory: Callable[[], VadSegmenter] | None = None,
        stream_factory: Callable | None = None,
    ):
        if segment_seconds is not None and segment_seconds <= 0:
            raise ValueError("segment_seconds must be positive")
        if segment_seconds is not None and channels != 1:
            raise ValueError("live VAD segmentation requires mono audio")
        if segment_queue_size < 1:
            raise ValueError("segment_queue_size must be at least 1")

        self.fs = int(samplerate)
        self.channels = int(channels)
        self.chunk_seconds = int(chunk_seconds)
        self.input_device = input_device
        self.prefer_pulse = prefer_pulse
        self.segment_seconds = (
            float(segment_seconds) if segment_seconds is not None else None
        )
        self.segment_queue_size = int(segment_queue_size)
        self._segmenter_factory = segmenter_factory
        self._stream_factory = stream_factory
        self.temp_dir = DATA_DIR / "temp"
        self.temp_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.temp_dir.chmod(0o700)
        self.is_recording = False
        self.last_temp_file: Path | None = None
        self.stream = None
        self._chunk_wave = None
        self._chunk_index = 0
        self._chunk_written_frames = 0
        self._chunk_target_frames = self.chunk_seconds * self.fs
        self._chunk_paths: list[Path] = []
        self._write_error: Exception | None = None
        self._segment_stream: _LiveSegmentStream | None = None
        self._segmenter: VadSegmenter | None = None
        self._segment_index = 0

    def start_recording(self) -> None:
        verbo("[recorder] Recording started")
        self._discard_chunks()
        self._write_error = None
        self._reset_segment_stream()
        self._open_new_chunk()

        preferred = self.input_device or ("pulse" if self.prefer_pulse else None)
        # Pinned/explicit inputs never fall back to another microphone. If the
        # default Pulse bridge is absent, PortAudio's default is still usable;
        # no positive mute verdict was made for that unpinned capture path.
        devices = [preferred]
        if not self.input_device and self._stream_factory is None and preferred is not None:
            devices.append(None)
        last_error = None
        for device in devices:
            rates = [self.fs, self._default_sample_rate(device)]
            for sample_rate in dict.fromkeys(rates):
                if sample_rate != self.fs:
                    self.fs = sample_rate
                    self._chunk_target_frames = self.chunk_seconds * self.fs
                    self._discard_chunks()
                    self._reset_segment_stream()
                    self._open_new_chunk()
                try:
                    self._start_stream(device, self.fs)
                    self.is_recording = True
                    return
                except Exception as exc:
                    last_error = exc
        self.is_recording = False
        self._discard_chunks()
        raise RuntimeError(f"could not open the selected audio input stream: {last_error}")

    def _start_stream(self, device, sample_rate) -> None:
        stream = self._open_stream(device, sample_rate)
        try:
            stream.start()
        except Exception:
            try:
                stream.close()
            except Exception:
                pass
            raise
        self.stream = stream

    def _open_stream(self, device, sample_rate):
        kwargs = {
            "samplerate": sample_rate,
            "channels": self.channels,
            "callback": self._audio_callback,
        }
        if device:
            kwargs["device"] = device
        return (self._stream_factory or sd.InputStream)(**kwargs)

    def _default_sample_rate(self, device) -> int:
        try:
            info = sd.query_devices(device, "input") if device else sd.query_devices(kind="input")
            return int(info.get("default_samplerate") or 48000)
        except Exception:
            return 48000

    def _audio_callback(self, indata, frames, _time, status) -> None:
        if status:
            verbo(f"[recorder] Warning: {status}")
        if self._write_error is not None:
            return
        try:
            pcm = (np.clip(indata.copy(), -1.0, 1.0) * 32767.0).astype(np.int16)
            pcm_bytes = pcm.tobytes()
            self._chunk_wave.writeframes(pcm_bytes)
            self._chunk_written_frames += frames
            try:
                self._capture_segment_frames(pcm_bytes, frames)
            except Exception as exc:
                if self._segment_stream is not None:
                    self._segment_stream.fail(exc)
                self._segmenter = None
                verr(f"[recorder] Live VAD failed; will replay after Stop: {exc}")
            if self._chunk_written_frames >= self._chunk_target_frames:
                self._close_chunk()
                self._open_new_chunk()
        except Exception as exc:
            self._write_error = exc
            verr(f"[recorder] Chunk write failed: {exc}")

    def stop_recording(self, preserve: bool = False) -> Path | None:
        if not self.is_recording:
            return None

        verbo("[recorder] Stopping recording")
        stop_error = None
        if self.stream is not None:
            try:
                self.stream.stop()
            except Exception as exc:
                stop_error = exc
            try:
                self.stream.close()
            except Exception as exc:
                stop_error = stop_error or exc
            self.stream = None
        self.is_recording = False
        try:
            self._close_chunk()
        finally:
            # A WAV flush/close failure must still release the live worker.
            # Otherwise iter_segments() keeps polling forever and the core
            # blocks permanently while joining its transcription thread.
            self._finish_segment_stream()

        recording_error = self._write_error or stop_error
        if preserve or recording_error:
            output_path = RECORDINGS_DIR / self._timestamped_filename()
        else:
            output_path = self.temp_dir / "last_recording.wav"
        self._stitch_chunks(output_path)
        self.last_temp_file = output_path
        verbo(f"[recorder] Saved to {output_path}")
        if recording_error:
            raise RuntimeError(
                f"audio capture failed; partial recording preserved at {output_path}: "
                f"{recording_error}"
            )
        return output_path

    def iter_segments(self) -> Iterator[tuple[int, bytes, bool]]:
        """Yield completed VAD-aligned WAV windows until recording stops.

        The queue is deliberately bounded. If transcription cannot keep pace,
        capture continues and this iterator fails so the caller can replay the
        complete stitched recording after Stop.
        """
        segment_stream = self._segment_stream
        if segment_stream is None:
            raise RuntimeError("live audio segments are not enabled")

        for index, pcm_bytes, frame_rate, speech_detected in segment_stream.iter_items():
            yield index, self._pcm_to_wav(pcm_bytes, frame_rate), speech_detected

    def _reset_segment_stream(self) -> None:
        self._segment_stream = (
            _LiveSegmentStream(capacity=self.segment_queue_size)
            if self.segment_seconds is not None
            else None
        )
        if self.segment_seconds is None:
            self._segmenter = None
        elif self._segmenter_factory is not None:
            self._segmenter = self._segmenter_factory()
        else:
            self._segmenter = VadSegmenter(target_seconds=self.segment_seconds)
        self._segment_index = 0

    def _capture_segment_frames(self, pcm_bytes: bytes, _frames: int) -> None:
        if (
            self._segment_stream is None
            or self._segment_stream.error is not None
            or self._segmenter is None
        ):
            return
        for segment, speech_detected in self._segmenter.feed(pcm_bytes, self.fs):
            if not self._queue_segment(segment, speech_detected):
                return

    def _queue_segment(self, pcm_bytes: bytes, speech_detected: bool) -> bool:
        if self._segment_stream is None:
            return False
        if not self._segment_stream.offer(
            (self._segment_index, pcm_bytes, self.fs, speech_detected)
        ):
            error = self._segment_stream.error
            if error is not None:
                verr(f"[recorder] {error}; will replay after Stop")
            return False
        self._segment_index += 1
        return True

    def _finish_segment_stream(self) -> None:
        if self._segment_stream is None:
            return
        final_item = None
        if self._segmenter is not None and self._segment_stream.error is None:
            final_segment = self._segmenter.finish()
        else:
            final_segment = None
        if final_segment:
            final_pcm, speech_detected = final_segment
            final_item = (
                self._segment_index,
                final_pcm,
                self.fs,
                speech_detected,
            )
        if self._segment_stream.close(final_item) and final_item is not None:
            self._segment_index += 1
        error = self._segment_stream.error
        if error is not None:
            verr(f"[recorder] {error}; will replay after Stop")
        self._segmenter = None

    def _pcm_to_wav(self, pcm_bytes: bytes, frame_rate: int) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as chunk:
            chunk.setnchannels(self.channels)
            chunk.setsampwidth(2)
            chunk.setframerate(frame_rate)
            chunk.writeframes(pcm_bytes)
        return output.getvalue()

    def _timestamped_filename(self) -> str:
        return f"{datetime.now():%Y%m%d_%H%M%S_%f}_recording.wav"

    def _open_new_chunk(self) -> None:
        self._chunk_index += 1
        self._chunk_written_frames = 0
        chunk_path = self.temp_dir / f"chunk_{self._chunk_index:04d}.wav"
        chunk_wave = wave.open(str(chunk_path), "wb")
        chunk_path.chmod(0o600)
        try:
            chunk_wave.setnchannels(self.channels)
            chunk_wave.setsampwidth(2)
            chunk_wave.setframerate(self.fs)
        except Exception:
            chunk_wave.close()
            try:
                chunk_path.unlink()
            except FileNotFoundError:
                pass
            raise
        self._chunk_paths.append(chunk_path)
        self._chunk_wave = chunk_wave

    def _close_chunk(self) -> None:
        if self._chunk_wave is not None:
            self._chunk_wave.close()
            self._chunk_wave = None

    def _discard_chunks(self) -> None:
        self._close_chunk()
        for path in self._chunk_paths:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        self._chunk_paths = []
        self._chunk_index = 0
        self._chunk_written_frames = 0

    def _stitch_chunks(self, output_path: Path) -> None:
        if not self._chunk_paths:
            raise RuntimeError("no recorded audio chunks were produced")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with wave.open(str(output_path), "wb") as destination:
                destination.setnchannels(self.channels)
                destination.setsampwidth(2)
                destination.setframerate(self.fs)
                for path in self._chunk_paths:
                    with wave.open(str(path), "rb") as source:
                        destination.writeframes(source.readframes(source.getnframes()))
        except Exception:
            # Preserve chunks for manual recovery when stitching fails.
            raise
        else:
            output_path.chmod(0o600)
            self._discard_chunks()
