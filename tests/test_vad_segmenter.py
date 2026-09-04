import hashlib
import io
import wave
from importlib.resources import files

import numpy as np
import pytest


class _SequencePredictor:
    def __init__(self, probabilities):
        self.probabilities = iter(probabilities)

    def __call__(self, _chunk):
        return next(self.probabilities)


def _probabilities_with_valleys(*valleys, frames=376):
    probabilities = np.full(frames, 0.9, dtype=np.float32)
    for seconds, risk in valleys:
        center = int(round(seconds / 0.032 - 0.5))
        probabilities[center - 1 : center + 2] = risk
    return probabilities


def _pcm(seconds, sample_rate=16_000):
    samples = np.arange(int(seconds * sample_rate), dtype=np.int64)
    return samples.astype("<i2").tobytes()


def _wav(path, pcm, sample_rate=16_000):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(pcm)


def _wav_pcm(wav_bytes):
    with wave.open(io.BytesIO(wav_bytes), "rb") as audio:
        return audio.readframes(audio.getnframes())


def test_minimum_local_risk_is_the_only_boundary_rule():
    from voxd.core.vad_segmenter import VadSegmenter

    probabilities = _probabilities_with_valleys((9.6, 0.4))
    source = _pcm(12.032)
    segmenter = VadSegmenter(
        target_seconds=10,
        predictor=_SequencePredictor(probabilities),
    )

    emitted = segmenter.feed(source, 16_000)
    remaining = segmenter.finish()

    assert len(emitted) == 1
    assert len(emitted[0][0]) / 2 / 16_000 == pytest.approx(9.6, abs=0.02)
    assert emitted[0][1] is True
    assert remaining[1] is True
    assert emitted[0][0] + remaining[0] == source


def test_lower_risk_beats_a_pause_closer_to_the_target():
    from voxd.core.vad_segmenter import VadSegmenter

    probabilities = _probabilities_with_valleys((8.5, 0.1), (10.0, 0.2))
    segmenter = VadSegmenter(
        target_seconds=10,
        predictor=_SequencePredictor(probabilities),
    )

    emitted = segmenter.feed(_pcm(12.032), 16_000)

    assert len(emitted) == 1
    assert len(emitted[0][0]) / 2 / 16_000 == pytest.approx(8.496, abs=0.02)


def test_resampling_preserves_every_original_pcm_frame():
    from voxd.core.vad_segmenter import VadSegmenter

    source = _pcm(12.032, sample_rate=48_000)
    probabilities = _probabilities_with_valleys((10.0, 0.1))
    segmenter = VadSegmenter(
        target_seconds=10,
        predictor=_SequencePredictor(probabilities),
    )

    emitted = segmenter.feed(source, 48_000)
    remaining = segmenter.finish()

    assert len(emitted) == 1
    assert emitted[0][0] + remaining[0] == source


def test_live_chunks_and_saved_wav_use_identical_boundaries(tmp_path):
    from voxd.core.vad_segmenter import VadSegmenter

    source = _pcm(25)
    probabilities = _probabilities_with_valleys(
        (9.6, 0.1),
        frames=int(25 / 0.032),
    )
    second_center = int(round(19.6 / 0.032 - 0.5))
    probabilities[second_center - 1 : second_center + 2] = 0.1

    live = VadSegmenter(
        target_seconds=10,
        predictor=_SequencePredictor(probabilities.copy()),
    )
    live_segments = []
    # Recorder callback sizes are backend-dependent and need not match the
    # saved-WAV adapter's 4096-frame reads.
    block_bytes = 777 * 2
    for start in range(0, len(source), block_bytes):
        live_segments.extend(live.feed(source[start : start + block_bytes], 16_000))
    final = live.finish()
    if final:
        live_segments.append(final)

    audio = tmp_path / "recording.wav"
    _wav(audio, source)
    saved = VadSegmenter(
        target_seconds=10,
        predictor=_SequencePredictor(probabilities.copy()),
    )
    saved_segments = [(wav, detected) for _, wav, detected in saved.iter_wav(audio)]

    assert [(_wav_pcm(segment), detected) for segment, detected in saved_segments] == live_segments
    assert b"".join(segment for segment, _ in live_segments) == source


def test_non_speech_audio_is_marked_without_being_discarded():
    from voxd.core.vad_segmenter import VadSegmenter

    probabilities = np.full(376, 0.1, dtype=np.float32)
    segmenter = VadSegmenter(
        target_seconds=10,
        predictor=_SequencePredictor(probabilities),
    )

    emitted = segmenter.feed(_pcm(12.032), 16_000)
    final = segmenter.finish()

    assert len(emitted) == 1
    assert emitted[0][1] is False
    assert final[1] is False
    assert emitted[0][0] + final[0] == _pcm(12.032)


def test_short_spoken_final_segment_is_retained():
    from voxd.core.vad_segmenter import VadSegmenter

    probabilities = np.asarray([0.1, 0.7, 0.1], dtype=np.float32)
    source = _pcm(0.096)
    segmenter = VadSegmenter(
        target_seconds=10,
        predictor=_SequencePredictor(probabilities),
    )

    assert segmenter.feed(source, 16_000) == []
    assert segmenter.finish() == (source, True)


def test_target_rejects_a_worst_case_segment_that_can_reach_30_seconds():
    from voxd.core.vad_segmenter import VadSegmenter

    with pytest.raises(ValueError, match="below 30 seconds"):
        VadSegmenter(target_seconds=27.99, predictor=lambda _chunk: 0.0)


def test_bundled_silero_model_has_the_pinned_digest():
    from voxd.core.vad_segmenter import VAD_MODEL_NAME, VAD_MODEL_SHA256

    model = files("voxd").joinpath("assets", VAD_MODEL_NAME)
    assert hashlib.sha256(model.read_bytes()).hexdigest() == VAD_MODEL_SHA256


def test_real_silero_model_marks_silence_when_runtime_is_available():
    pytest.importorskip("onnxruntime")
    from voxd.core.vad_segmenter import VadSegmenter

    segmenter = VadSegmenter(target_seconds=10)
    emitted = segmenter.feed(bytes(int(12.032 * 16_000) * 2), 16_000)

    assert len(emitted) == 1
    assert emitted[0][1] is False
    assert segmenter.finish()[1] is False
