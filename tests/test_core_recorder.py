import io
import stat
import wave
from threading import Event, Thread

import numpy as np
import pytest


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


def test_recorder_streams_overlapping_windows_before_stop(monkeypatch):
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
        segment_overlap_seconds=0.10,
    )
    monkeypatch.setattr(recorder, "_open_stream", lambda *_: SilentStream())
    recorder.start_recording()
    recorder._audio_callback(np.zeros((60, 1), dtype=np.float32), 60, None, None)

    first = next(recorder.iter_segments())
    assert recorder.is_recording is True

    recorder.stop_recording()
    remaining = list(recorder.iter_segments())
    segments = [first, *remaining]
    assert [index for index, _ in segments] == [0, 1, 2, 3]

    durations = []
    for _, wav_bytes in segments:
        with wave.open(io.BytesIO(wav_bytes), "rb") as audio:
            durations.append(audio.getnframes() / audio.getframerate())
    assert durations == [0.25, 0.25, 0.25, 0.15]


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
        segment_overlap_seconds=0.10,
        segment_queue_size=1,
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
        segment_overlap_seconds=0.10,
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
    assert [index for index, _ in segments] == [0]


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

    assert stream.close((0, b"final audio", 16_000)) is True
    consumer.join(timeout=1)

    assert not consumer.is_alive()
    assert received == [(0, b"final audio", 16_000)]
