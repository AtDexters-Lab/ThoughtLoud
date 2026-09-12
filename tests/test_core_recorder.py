import io
import stat
import wave
from threading import Event, Thread

import numpy as np
import pytest


class _FixedSegmenter:
    def __init__(self, frames):
        self.frames = frames
        self.buffer = bytearray()

    def feed(self, pcm_bytes, _sample_rate):
        self.buffer.extend(pcm_bytes)
        byte_count = self.frames * 2
        emitted = []
        while len(self.buffer) >= byte_count:
            emitted.append((bytes(self.buffer[:byte_count]), True))
            del self.buffer[:byte_count]
        return emitted

    def finish(self):
        if not self.buffer:
            return None
        final = bytes(self.buffer)
        self.buffer.clear()
        return final, True


def test_recorder_start_stop_creates_pcm_wav():
    from voxd.core.recorder import AudioRecorder

    recorder = AudioRecorder(samplerate=16000, channels=1, chunk_seconds=300)
    recorder.start_recording()
    output = recorder.stop_recording()

    assert output is not None and output.exists()
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert stat.S_IMODE(output.parent.stat().st_mode) == 0o700
    with wave.open(str(output), "rb") as audio:
        assert audio.getframerate() == 16000
        assert audio.getnchannels() == 1
        assert audio.getnframes() > 0


def test_recorder_rotates_chunks_without_limiting_recording(monkeypatch):
    from voxd.core.recorder import AudioRecorder

    recorder = AudioRecorder(samplerate=16000, channels=1, chunk_seconds=300)
    recorder._chunk_target_frames = 1
    recorder.start_recording()

    assert recorder.is_recording is True
    assert len(recorder._chunk_paths) >= 2
    output = recorder.stop_recording()
    assert output is not None and output.exists()


def test_recorder_preserves_partial_audio_when_stream_stop_fails():
    from voxd.core.recorder import AudioRecorder

    recorder = AudioRecorder(samplerate=16000, channels=1)
    recorder.start_recording()
    recorder.stream.stop = lambda: (_ for _ in ()).throw(RuntimeError("device lost"))

    with pytest.raises(RuntimeError, match="partial recording preserved"):
        recorder.stop_recording()

    assert recorder.last_temp_file is not None
    assert recorder.last_temp_file.exists()


def test_recorder_streams_vad_segments_before_stop(monkeypatch):
    from voxd.core.recorder import AudioRecorder

    class SilentStream:
        def start(self):
            return None

        def stop(self):
            return None

        def close(self):
            return None

    recorder = AudioRecorder(
        samplerate=100,
        channels=1,
        segment_seconds=0.25,
        segmenter_factory=lambda: _FixedSegmenter(25),
    )
    monkeypatch.setattr(recorder, "_open_stream", lambda *_: SilentStream())
    recorder.start_recording()
    recorder._audio_callback(np.zeros((60, 1), dtype=np.float32), 60, None, None)

    first = next(recorder.iter_segments())
    assert recorder.is_recording is True

    recorder.stop_recording()
    remaining = list(recorder.iter_segments())
    segments = [first, *remaining]
    assert [index for index, _, _ in segments] == [0, 1, 2]
    assert all(speech_detected for _, _, speech_detected in segments)

    durations = []
    for _, wav_bytes, _ in segments:
        with wave.open(io.BytesIO(wav_bytes), "rb") as audio:
            durations.append(audio.getnframes() / audio.getframerate())
    assert durations == [0.25, 0.25, 0.10]


def test_recorder_bounds_live_queue_and_preserves_capture(monkeypatch):
    from voxd.core.recorder import AudioRecorder

    class SilentStream:
        def start(self):
            return None

        def stop(self):
            return None

        def close(self):
            return None

    recorder = AudioRecorder(
        samplerate=100,
        channels=1,
        segment_seconds=0.25,
        segment_queue_size=1,
        segmenter_factory=lambda: _FixedSegmenter(25),
    )
    monkeypatch.setattr(recorder, "_open_stream", lambda *_: SilentStream())
    recorder.start_recording()
    recorder._audio_callback(np.zeros((60, 1), dtype=np.float32), 60, None, None)
    output = recorder.stop_recording()

    assert output is not None and output.exists()
    with wave.open(str(output), "rb") as audio:
        assert audio.getnframes() == 60
    with pytest.raises(RuntimeError, match="transcription fell more than 1 segment behind"):
        next(recorder.iter_segments())


def test_recorder_preserves_capture_when_live_vad_fails(monkeypatch):
    from voxd.core.recorder import AudioRecorder

    class SilentStream:
        def start(self):
            return None

        def stop(self):
            return None

        def close(self):
            return None

    class FailingSegmenter:
        def feed(self, _pcm_bytes, _sample_rate):
            raise RuntimeError("VAD inference failed")

        def finish(self):
            raise AssertionError("failed VAD must not be finalized")

    recorder = AudioRecorder(
        samplerate=100,
        channels=1,
        segment_seconds=0.25,
        segmenter_factory=FailingSegmenter,
    )
    monkeypatch.setattr(recorder, "_open_stream", lambda *_: SilentStream())
    recorder.start_recording()

    block = np.zeros((60, 1), dtype=np.float32)
    recorder._audio_callback(block, 60, None, None)
    recorder._audio_callback(block, 60, None, None)
    output = recorder.stop_recording()

    with pytest.raises(RuntimeError, match="VAD inference failed"):
        list(recorder.iter_segments())
    with wave.open(str(output), "rb") as audio:
        assert audio.getnframes() == 120


def test_recorder_closes_live_segment_stream_when_wav_close_fails(monkeypatch):
    from voxd.core.recorder import AudioRecorder

    class SilentStream:
        def start(self):
            return None

        def stop(self):
            return None

        def close(self):
            return None

    recorder = AudioRecorder(
        samplerate=100,
        channels=1,
        segment_seconds=0.25,
        segmenter_factory=lambda: _FixedSegmenter(25),
    )
    monkeypatch.setattr(recorder, "_open_stream", lambda *_: SilentStream())
    recorder.start_recording()
    recorder._audio_callback(np.zeros((10, 1), dtype=np.float32), 10, None, None)

    original_close = recorder._close_chunk

    def close_then_fail():
        original_close()
        raise OSError("disk full while closing WAV")

    monkeypatch.setattr(recorder, "_close_chunk", close_then_fail)

    with pytest.raises(OSError, match="disk full"):
        recorder.stop_recording()

    segments = list(recorder.iter_segments())
    assert [index for index, _, _ in segments] == [0]


def test_live_segment_stream_drains_final_item_before_closing():
    from voxd.core.recorder import _LiveSegmentStream

    stream = _LiveSegmentStream(capacity=1)
    consumer_started = Event()
    received = []

    def consume():
        consumer_started.set()
        received.extend(stream.iter_items())

    consumer = Thread(target=consume)
    consumer.start()
    assert consumer_started.wait(timeout=1)

    assert stream.close((0, b"final audio", 16_000, False)) is True
    consumer.join(timeout=1)

    assert not consumer.is_alive()
    assert received == [(0, b"final audio", 16_000, False)]


def test_explicit_microphone_does_not_fall_back_to_default(monkeypatch):
    from voxd.core.recorder import AudioRecorder
    recorder = AudioRecorder(input_device="USB mic")
    devices = []
    def fail(device, _rate):
        devices.append(device)
        raise RuntimeError("disconnected")
    monkeypatch.setattr(recorder, "_open_stream", fail)
    with pytest.raises(RuntimeError, match="selected audio input"):
        recorder.start_recording()
    assert set(devices) == {"USB mic"}


def test_pinned_stream_failure_does_not_open_unrelated_input(monkeypatch):
    from voxd.core.recorder import AudioRecorder
    calls = []
    def fail(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("source disconnected")
    recorder = AudioRecorder(stream_factory=fail)
    with pytest.raises(RuntimeError, match="selected audio input"):
        recorder.start_recording()
    assert calls
    assert {call.get("device") for call in calls} == {"pulse"}


def test_default_capture_uses_portaudio_default_if_pulse_bridge_absent(monkeypatch):
    from voxd.core.recorder import AudioRecorder
    recorder = AudioRecorder()
    original = recorder._open_stream
    devices = []
    def open_stream(device, rate):
        devices.append(device)
        if device == "pulse":
            raise RuntimeError("no pulse plugin")
        return original(device, rate)
    monkeypatch.setattr(recorder, "_open_stream", open_stream)
    recorder.start_recording()
    recorder.stop_recording()
    assert devices[-1] is None
