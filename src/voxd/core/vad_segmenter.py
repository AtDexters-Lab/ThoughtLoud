from __future__ import annotations

import io
import os
import wave
from importlib.resources import files
from pathlib import Path
from typing import Callable, Iterator

import numpy as np


# VOXD's audio pipeline is local. Disable the official ONNX Runtime build's
# telemetry before its first import, including creation of a persistent device ID.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")


VAD_SAMPLE_RATE = 16_000
VAD_FRAME_SAMPLES = 512
VAD_FRAME_SECONDS = VAD_FRAME_SAMPLES / VAD_SAMPLE_RATE
VAD_LOCAL_WINDOW_FRAMES = 3
VAD_LOCAL_WINDOW_MS = VAD_LOCAL_WINDOW_FRAMES * VAD_FRAME_SECONDS * 1000
VAD_DECISION_LOOKAHEAD_MS = VAD_FRAME_SECONDS * 1000
VAD_SEARCH_RADIUS_SECONDS = 2.0
VAD_SPEECH_THRESHOLD = 0.5
VAD_MODEL_NAME = "silero_vad_16k_op15.onnx"
VAD_MODEL_SHA256 = "7ed98ddbad84ccac4cd0aeb3099049280713df825c610a8ed34543318f1b2c49"


VadPcmSegment = tuple[bytes, bool]


class VadSegmentationError(RuntimeError):
    """Raised when PCM cannot be segmented safely."""


class _StreamingLinearResampler:
    """Convert an arbitrary mono input rate to a continuous 16 kHz stream."""

    def __init__(self, source_rate: int):
        if source_rate <= 0:
            raise ValueError("source_rate must be positive")
        self.source_rate = int(source_rate)
        self._step = self.source_rate / VAD_SAMPLE_RATE
        self._source_samples = 0
        self._next_source_position = 0.0
        self._previous_sample: float | None = None

    def feed(self, samples: np.ndarray) -> np.ndarray:
        source = np.asarray(samples, dtype=np.float32).reshape(-1)
        if source.size == 0:
            return np.empty(0, dtype=np.float32)
        if self.source_rate == VAD_SAMPLE_RATE:
            self._source_samples += int(source.size)
            return source / 32768.0

        start = self._source_samples
        stop = start + int(source.size)
        if self._previous_sample is None:
            values = source
            first_position = float(start)
        else:
            values = np.concatenate(
                (np.asarray([self._previous_sample], dtype=np.float32), source)
            )
            first_position = float(start - 1)

        last_position = float(stop - 1)
        if self._next_source_position > last_position:
            output = np.empty(0, dtype=np.float32)
        else:
            positions = np.arange(
                self._next_source_position,
                last_position + 1e-9,
                self._step,
                dtype=np.float64,
            )
            coordinates = np.arange(values.size, dtype=np.float64) + first_position
            output = np.interp(positions, coordinates, values).astype(np.float32)
            self._next_source_position += positions.size * self._step

        self._source_samples = stop
        self._previous_sample = float(source[-1])
        return output / 32768.0


class _SileroOnnxPredictor:
    """Minimal stateful wrapper around the bundled Silero ONNX model."""

    def __init__(self, model_path: Path | None = None):
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise VadSegmentationError(
                "Silero VAD requires onnxruntime; reinstall VOXD dependencies"
            ) from exc

        if model_path is None:
            model_path = Path(files("voxd").joinpath("assets", VAD_MODEL_NAME))
        if not model_path.is_file():
            raise VadSegmentationError(f"Silero VAD model not found: {model_path}")

        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
            sess_options=options,
        )
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, 64), dtype=np.float32)

    def __call__(self, chunk: np.ndarray) -> float:
        current = np.asarray(chunk, dtype=np.float32).reshape(1, -1)
        if current.shape != (1, VAD_FRAME_SAMPLES):
            raise ValueError(f"Silero requires exactly {VAD_FRAME_SAMPLES} samples")
        model_input = np.concatenate((self._context, current), axis=1)
        probability, self._state = self._session.run(
            None,
            {
                "input": model_input,
                "state": self._state,
                "sr": np.asarray(VAD_SAMPLE_RATE, dtype=np.int64),
            },
        )
        self._context = model_input[:, -64:]
        return float(probability[0, 0])


class VadSegmenter:
    """Split mono 16-bit PCM near a target duration at minimum VAD risk."""

    def __init__(
        self,
        *,
        target_seconds: float = 10.0,
        search_radius_seconds: float = VAD_SEARCH_RADIUS_SECONDS,
        predictor: Callable[[np.ndarray], float] | None = None,
    ):
        if target_seconds <= search_radius_seconds:
            raise ValueError("target_seconds must exceed the VAD search radius")
        if (
            target_seconds + search_radius_seconds + VAD_FRAME_SECONDS
            >= 30
        ):
            raise ValueError("the longest VAD segment must remain below 30 seconds")
        self.target_seconds = float(target_seconds)
        self.search_radius_seconds = float(search_radius_seconds)
        self._predictor = predictor or _SileroOnnxPredictor()
        self._source_rate: int | None = None
        self._resampler: _StreamingLinearResampler | None = None
        self._pcm = bytearray()
        self._pcm_start_frame = 0
        self._source_frames = 0
        self._vad_pending = np.empty(0, dtype=np.float32)
        self._probabilities: list[float] = []

    @property
    def maximum_segment_seconds(self) -> float:
        return self.target_seconds + self.search_radius_seconds

    def feed(self, pcm_bytes: bytes, sample_rate: int) -> list[VadPcmSegment]:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if len(pcm_bytes) % 2:
            raise VadSegmentationError("PCM input must contain complete 16-bit samples")
        if not pcm_bytes:
            return []

        if self._source_rate is None:
            self._source_rate = int(sample_rate)
            self._resampler = _StreamingLinearResampler(self._source_rate)
        elif sample_rate != self._source_rate:
            raise VadSegmentationError("PCM sample rate changed during one recording")

        source = np.frombuffer(pcm_bytes, dtype="<i2")
        self._pcm.extend(pcm_bytes)
        self._source_frames += int(source.size)
        analysis = self._resampler.feed(source)
        if self._vad_pending.size:
            analysis = np.concatenate((self._vad_pending, analysis))
        complete = analysis.size // VAD_FRAME_SAMPLES
        for index in range(complete):
            start = index * VAD_FRAME_SAMPLES
            chunk = analysis[start : start + VAD_FRAME_SAMPLES]
            self._probabilities.append(float(self._predictor(chunk)))
        self._vad_pending = analysis[complete * VAD_FRAME_SAMPLES :].copy()

        emitted: list[VadPcmSegment] = []
        while self._captured_seconds() >= (
            self._pcm_start_seconds()
            + self.maximum_segment_seconds
            + VAD_FRAME_SECONDS
        ):
            interval_start = self._pcm_start_seconds()
            cut_seconds = self._select_cut_seconds()
            cut_frame = int(round(cut_seconds * self._source_rate))
            relative_frames = cut_frame - self._pcm_start_frame
            if relative_frames <= 0:
                raise VadSegmentationError("VAD selected a non-advancing boundary")
            byte_count = relative_frames * 2
            segment = bytes(self._pcm[:byte_count])
            del self._pcm[:byte_count]
            self._pcm_start_frame = cut_frame
            emitted.append(
                (segment, self._interval_has_speech(interval_start, cut_seconds))
            )
        return emitted

    def finish(self) -> VadPcmSegment | None:
        if not self._pcm:
            return None
        interval_start = self._pcm_start_seconds()
        interval_stop = self._captured_seconds()
        remaining = bytes(self._pcm)
        self._pcm.clear()
        self._pcm_start_frame = self._source_frames
        return remaining, self._interval_has_speech(interval_start, interval_stop)

    def iter_wav(
        self, audio_path: Path, *, read_frames: int = 4096
    ) -> Iterator[tuple[int, bytes, bool]]:
        try:
            source = wave.open(str(audio_path), "rb")
        except (wave.Error, OSError) as exc:
            raise VadSegmentationError(f"Could not read PCM WAV: {exc}") from exc

        index = 0
        with source:
            if source.getnchannels() != 1:
                raise VadSegmentationError("Silero segmentation requires mono audio")
            if source.getsampwidth() != 2:
                raise VadSegmentationError("Silero segmentation requires 16-bit PCM audio")
            if source.getcomptype() != "NONE":
                raise VadSegmentationError("Silero segmentation requires uncompressed PCM")
            sample_rate = source.getframerate()
            while True:
                pcm = source.readframes(read_frames)
                if not pcm:
                    break
                for segment, speech_detected in self.feed(pcm, sample_rate):
                    yield index, self._pcm_to_wav(segment, sample_rate), speech_detected
                    index += 1

            final = self.finish()
            if final:
                segment, speech_detected = final
                yield index, self._pcm_to_wav(segment, sample_rate), speech_detected

    def _captured_seconds(self) -> float:
        return self._source_frames / self._source_rate

    def _pcm_start_seconds(self) -> float:
        return self._pcm_start_frame / self._source_rate

    def _select_cut_seconds(self) -> float:
        start = self._pcm_start_seconds()
        target = start + self.target_seconds
        lower = target - self.search_radius_seconds
        upper = target + self.search_radius_seconds
        # A candidate always needs one completed VAD frame on either side so
        # the three-frame score is independent of recorder callback size.
        first = max(1, int(np.ceil(lower / VAD_FRAME_SECONDS - 0.5)))
        last = min(
            len(self._probabilities) - 1,
            int(np.floor(upper / VAD_FRAME_SECONDS - 0.5)) + 1,
        )
        if first >= last:
            raise VadSegmentationError("VAD produced no probabilities in the search window")

        candidates: list[tuple[float, float, float]] = []
        for index in range(first, last):
            risk = float(np.mean(self._probabilities[index - 1 : index + 2]))
            cut = (index + 0.5) * VAD_FRAME_SECONDS
            candidates.append((risk, abs(cut - target), cut))
        return min(candidates)[2]

    def _interval_has_speech(self, start_seconds: float, stop_seconds: float) -> bool:
        first = max(0, int(np.floor(start_seconds / VAD_FRAME_SECONDS)))
        last = min(
            len(self._probabilities),
            int(np.ceil(stop_seconds / VAD_FRAME_SECONDS)),
        )
        return any(
            probability >= VAD_SPEECH_THRESHOLD
            for probability in self._probabilities[first:last]
        )

    @staticmethod
    def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as chunk:
            chunk.setnchannels(1)
            chunk.setsampwidth(2)
            chunk.setframerate(sample_rate)
            chunk.writeframes(pcm_bytes)
        return output.getvalue()
