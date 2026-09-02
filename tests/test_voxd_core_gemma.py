import hashlib
from threading import Event, Thread
from types import SimpleNamespace


def _config(*, recording_archive_enabled=False):
    return SimpleNamespace(
        gemma_server_url="http://localhost:9292",
        gemma_model="gemma-e4b",
        gemma_segment_seconds=25,
        gemma_segment_overlap_seconds=1,
        gemma_timeout=300,
        gemma_max_tokens=1024,
        record_chunk_seconds=300,
        recording_archive_enabled=recording_archive_enabled,
        recording_archive_max_mb=5120,
        audio_input_device="",
        audio_prefer_pulse=True,
        typing_delay=2,
        typing_word_delay=10,
        typing_start_delay=0,
        data={"append_trailing_space": True},
    )


def _install_fakes(
    monkeypatch,
    tmp_path,
    *,
    clipboard_error=None,
    warmup_error=None,
    recording_stop_error=None,
    archive_error=None,
    live_error=None,
    replay_error=None,
    segment_before_stop=False,
):
    import voxd.core.archive as archive_module
    import voxd.core.clipboard as clipboard_module
    import voxd.core.recorder as recorder_module
    import voxd.core.typer as typer_module
    import voxd.core.voxd_core as core_module
    from voxd.core.gemma_transcriber import (
        StreamingShadow,
        StreamingShadowEvent,
        TranscriptionResult,
    )

    events = {
        "transcriber_kwargs": None,
        "typed": [],
        "copied": [],
        "order": [],
        "preserve": [],
        "archives": [],
        "cleaned": [],
        "live_segment_seen": Event(),
    }
    recording_path = tmp_path / "recording.wav"

    class FakeRecorder:
        def __init__(self, **kwargs):
            assert kwargs["chunk_seconds"] == 300
            assert kwargs["segment_seconds"] == 25
            assert kwargs["segment_overlap_seconds"] == 1
            self.is_recording = False
            self.last_temp_file = None
            self.stopped = Event()

        def start_recording(self):
            self.is_recording = True
            events["order"].append("recording-started")

        def stop_recording(self, preserve=False):
            events["preserve"].append(preserve)
            self.is_recording = False
            self.stopped.set()
            if preserve:
                recording_path.write_bytes(b"recording")
            if recording_stop_error:
                self.last_temp_file = recording_path
                raise recording_stop_error
            return recording_path

        def iter_segments(self):
            if not segment_before_stop:
                assert self.stopped.wait(timeout=1)
            yield 0, b"recording segment"
            if segment_before_stop:
                assert self.stopped.wait(timeout=1)

    class FakeTranscriber:
        def __init__(self, **kwargs):
            events["transcriber_kwargs"] = kwargs
            self.prompt = "test prompt"
            self.resolved_model = "resolved-e4b"
            self.system_fingerprint = "test-fingerprint"

        def warmup(self):
            events["order"].append("warmup")
            if warmup_error:
                raise warmup_error

        def transcribe_segments(self, segments):
            received = []
            for segment in segments:
                received.append(segment)
                events["live_segment_seen"].set()
            assert received == [(0, b"recording segment")]
            events["order"].append("transcribe-live")
            if live_error:
                # Simulate identity observed by a discarded partial live pass.
                self.resolved_model = "discarded-live-model"
                self.system_fingerprint = "discarded-live-fingerprint"
                raise live_error
            return TranscriptionResult(
                text="namaste doston",
                segments=("namaste", "doston"),
                resolved_model="resolved-e4b",
                system_fingerprint="test-fingerprint",
                streaming_shadow=StreamingShadow(
                    events=(
                        StreamingShadowEvent(
                            segment_index=1,
                            elapsed_seconds=1.2345,
                            committed_delta_characters=7,
                            committed_characters=7,
                            provisional_characters=6,
                            overlap_words=2,
                            boundary_matched=True,
                            commits_blocked=False,
                        ),
                    ),
                    committed_characters=7,
                    provisional_characters=6,
                    stalled_boundaries=0,
                    final_matches=True,
                ),
            )

        def transcribe(self, path):
            assert path == recording_path
            events["order"].append("transcribe-replay")
            if replay_error:
                raise replay_error
            return TranscriptionResult(
                text="namaste doston",
                segments=("namaste", "doston"),
                resolved_model=None,
                system_fingerprint=None,
                streaming_shadow=StreamingShadow(
                    events=(
                        StreamingShadowEvent(
                            segment_index=0,
                            elapsed_seconds=0.1,
                            committed_delta_characters=0,
                            committed_characters=0,
                            provisional_characters=7,
                            overlap_words=0,
                            boundary_matched=False,
                            commits_blocked=False,
                        ),
                        StreamingShadowEvent(
                            segment_index=1,
                            elapsed_seconds=0.2,
                            committed_delta_characters=0,
                            committed_characters=0,
                            provisional_characters=14,
                            overlap_words=0,
                            boundary_matched=False,
                            commits_blocked=True,
                        ),
                    ),
                    committed_characters=0,
                    provisional_characters=14,
                    stalled_boundaries=1,
                    final_matches=True,
                ),
            )

        def cleanup_input(self, path):
            events["cleaned"].append(path)

    class FakeArchive:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def store(self, path, metadata):
            if archive_error:
                raise archive_error
            events["archives"].append((self.kwargs, path, metadata))
            return path.with_suffix(".flac")

    class FakeTyper:
        def __init__(self, delay, word_delay, start_delay, cfg):
            assert delay == 2
            assert word_delay == 10
            assert start_delay == 0

        def type(self, text):
            events["typed"].append(text)

    class FakeClipboard:
        def copy(self, text):
            if clipboard_error:
                raise clipboard_error
            events["copied"].append(text)

    monkeypatch.setattr(recorder_module, "AudioRecorder", FakeRecorder)
    monkeypatch.setattr(archive_module, "RecordingArchive", FakeArchive)
    monkeypatch.setattr(core_module, "GemmaAudioTranscriber", FakeTranscriber)
    monkeypatch.setattr(typer_module, "YdotoolTyper", FakeTyper)
    monkeypatch.setattr(clipboard_module, "ClipboardManager", FakeClipboard)
    return events


def test_core_process_uses_gemma_and_types_final_text(monkeypatch, tmp_path):
    from voxd.core.voxd_core import CoreProcessThread

    events = _install_fakes(monkeypatch, tmp_path)
    finished = []
    thread = CoreProcessThread(_config())
    thread.should_stop = True
    thread.finished.connect(finished.append)
    thread.run()

    assert events["transcriber_kwargs"]["segment_seconds"] == 25
    assert events["transcriber_kwargs"]["delete_input"] is True
    assert events["preserve"] == [False]
    assert events["archives"] == []
    assert events["typed"] == ["namaste doston"]
    assert events["copied"] == ["namaste doston"]
    assert events["order"] == ["recording-started", "warmup", "transcribe-live"]
    assert len(events["cleaned"]) == 1
    assert events["cleaned"][0].name == "recording.wav"
    assert finished == ["namaste doston"]


def test_archive_enabled_preserves_audio_and_records_replay_metadata(monkeypatch, tmp_path):
    from voxd.core.voxd_core import CoreProcessThread

    events = _install_fakes(monkeypatch, tmp_path)
    finished = []
    thread = CoreProcessThread(_config(recording_archive_enabled=True))
    thread.should_stop = True
    thread.finished.connect(finished.append)
    thread.run()

    assert events["transcriber_kwargs"]["delete_input"] is False
    assert events["preserve"] == [True]
    assert len(events["archives"]) == 1
    kwargs, recording_path, metadata = events["archives"][0]
    assert kwargs["max_bytes"] == 5120 * 1024 * 1024
    assert recording_path.name == "recording.wav"
    transcription = metadata["transcription"]
    assert transcription["status"] == "complete"
    assert transcription["configured_model"] == "gemma-e4b"
    assert transcription["model"] == "resolved-e4b"
    assert transcription["system_fingerprint"] == "test-fingerprint"
    assert transcription["text"] == "namaste doston"
    assert transcription["segments"] == ["namaste", "doston"]
    assert transcription["prompt"] == "test prompt"
    assert transcription["prompt_sha256"] == hashlib.sha256(b"test prompt").hexdigest()
    assert transcription["streaming_shadow"] == {
        "source": "live",
        "committed_characters": 7,
        "provisional_characters": 6,
        "stalled_boundaries": 0,
        "final_matches": True,
        "events": [
            {
                "segment_index": 1,
                "elapsed_seconds": 1.234,
                "committed_delta_characters": 7,
                "committed_characters": 7,
                "provisional_characters": 6,
                "overlap_words": 2,
                "boundary_matched": True,
                "commits_blocked": False,
            }
        ],
    }
    assert events["typed"] == ["namaste doston"]
    assert finished == ["namaste doston"]


def test_core_decodes_completed_segment_before_recording_stops(monkeypatch, tmp_path):
    from voxd.core.voxd_core import CoreProcessThread

    events = _install_fakes(monkeypatch, tmp_path, segment_before_stop=True)
    thread = CoreProcessThread(_config())
    runner = Thread(target=thread.run)
    runner.start()

    assert events["live_segment_seen"].wait(timeout=1)
    assert runner.is_alive()
    thread.stop_recording()
    runner.join(timeout=1)

    assert not runner.is_alive()
    assert events["typed"] == ["namaste doston"]


def test_archive_recovers_partial_wav_when_recorder_stop_raises(monkeypatch, tmp_path):
    from voxd.core.voxd_core import CoreProcessThread

    events = _install_fakes(
        monkeypatch,
        tmp_path,
        recording_stop_error=RuntimeError("capture failed"),
    )
    finished = []
    thread = CoreProcessThread(_config(recording_archive_enabled=True))
    thread.should_stop = True
    thread.finished.connect(finished.append)
    thread.run()

    assert len(events["archives"]) == 1
    _, recording_path, metadata = events["archives"][0]
    assert recording_path.exists()
    assert metadata["transcription"]["status"] == "failed"
    assert metadata["transcription"]["error"] == "capture failed"
    assert events["typed"] == []
    assert finished == [""]


def test_archive_failure_does_not_change_completed_dictation(monkeypatch, tmp_path):
    from voxd.core.voxd_core import CoreProcessThread

    events = _install_fakes(
        monkeypatch,
        tmp_path,
        archive_error=RuntimeError("disk unavailable"),
    )
    finished = []
    thread = CoreProcessThread(_config(recording_archive_enabled=True))
    thread.should_stop = True
    thread.finished.connect(finished.append)
    thread.run()

    assert events["typed"] == ["namaste doston"]
    assert finished == ["namaste doston"]


def test_clipboard_failure_does_not_block_real_typing(monkeypatch, tmp_path):
    from voxd.core.voxd_core import CoreProcessThread

    events = _install_fakes(monkeypatch, tmp_path, clipboard_error=RuntimeError("no clipboard"))
    finished = []
    thread = CoreProcessThread(_config())
    thread.should_stop = True
    thread.finished.connect(finished.append)
    thread.run()

    assert events["typed"] == ["namaste doston"]
    assert finished == ["namaste doston"]


def test_warmup_failure_does_not_block_transcription(monkeypatch, tmp_path):
    from voxd.core.voxd_core import CoreProcessThread

    events = _install_fakes(
        monkeypatch,
        tmp_path,
        warmup_error=RuntimeError("warmup unavailable"),
    )
    finished = []
    thread = CoreProcessThread(_config())
    thread.should_stop = True
    thread.finished.connect(finished.append)
    thread.run()

    assert events["order"] == ["recording-started", "warmup", "transcribe-live"]
    assert events["typed"] == ["namaste doston"]
    assert finished == ["namaste doston"]


def test_live_transcription_failure_replays_complete_recording(monkeypatch, tmp_path):
    from voxd.core.voxd_core import CoreProcessThread

    events = _install_fakes(
        monkeypatch,
        tmp_path,
        live_error=RuntimeError("server restarted"),
    )
    finished = []
    thread = CoreProcessThread(_config())
    thread.should_stop = True
    thread.finished.connect(finished.append)
    thread.run()

    assert events["order"] == [
        "recording-started",
        "warmup",
        "transcribe-live",
        "transcribe-replay",
    ]
    assert events["typed"] == ["namaste doston"]
    assert events["cleaned"] == []
    assert finished == ["namaste doston"]


def test_fallback_archive_uses_only_replay_model_identity(monkeypatch, tmp_path):
    from voxd.core.voxd_core import CoreProcessThread

    events = _install_fakes(
        monkeypatch,
        tmp_path,
        live_error=RuntimeError("endpoint restarted"),
    )
    thread = CoreProcessThread(_config(recording_archive_enabled=True))
    thread.should_stop = True
    thread.run()

    assert len(events["archives"]) == 1
    transcription = events["archives"][0][2]["transcription"]
    assert transcription["status"] == "complete"
    assert transcription["text"] == "namaste doston"
    assert transcription["model"] is None
    assert transcription["system_fingerprint"] is None
    assert transcription["streaming_shadow"]["source"] == "replay"
    assert transcription["streaming_shadow"]["final_matches"] is True


def test_live_and_replay_failure_still_archives_full_audio(monkeypatch, tmp_path):
    from voxd.core.voxd_core import CoreProcessThread

    events = _install_fakes(
        monkeypatch,
        tmp_path,
        live_error=RuntimeError("live server restarted"),
        replay_error=RuntimeError("server offline"),
    )
    finished = []
    thread = CoreProcessThread(_config(recording_archive_enabled=True))
    thread.should_stop = True
    thread.finished.connect(finished.append)
    thread.run()

    assert len(events["archives"]) == 1
    _, recording_path, metadata = events["archives"][0]
    assert recording_path.exists()
    assert metadata["transcription"]["status"] == "failed"
    assert metadata["transcription"]["error"] == "server offline"
    assert metadata["transcription"]["segments"] == []
    assert events["typed"] == []
    assert finished == [""]
