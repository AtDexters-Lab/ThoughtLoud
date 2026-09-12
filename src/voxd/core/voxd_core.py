from __future__ import annotations

from copy import deepcopy
from threading import Event

from PyQt6.QtCore import QThread, pyqtSignal

from voxd.core.gemma_transcriber import GemmaAudioTranscriber
from voxd.core.session import DictationSession


class _ManagedTranscriber:
    """Wait for our server on the decode thread, after capture has started."""

    def __init__(self, transcriber, lease, timeout):
        self._transcriber = transcriber
        self._lease = lease
        self._timeout = timeout

    def __getattr__(self, name):
        return getattr(self._transcriber, name)

    def warmup(self):
        self._lease.wait_ready(timeout=self._timeout)
        return self._transcriber.warmup()

    def transcribe_segments(self, segments):
        self._lease.wait_ready(timeout=self._timeout)
        return self._transcriber.transcribe_segments(segments)

    def transcribe(self, path):
        self._lease.wait_ready(timeout=self._timeout)
        return self._transcriber.transcribe(path)


class CoreProcessThread(QThread):
    """Qt/Linux wiring for the UI-independent dictation session."""

    finished = pyqtSignal(str)
    status_changed = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, cfg, *, recorder_factory=None, runtime=None):
        super().__init__()
        self.cfg = deepcopy(cfg)
        self._runtime = runtime
        self._recorder_factory = recorder_factory
        self._stop_event = Event()
        # Settings changes during recording apply to the next dictation only.
        self._speech_preferences = getattr(cfg, "speech_preferences", "")

    @property
    def should_stop(self) -> bool:
        return self._stop_event.is_set()

    @should_stop.setter
    def should_stop(self, value: bool) -> None:
        if value:
            self._stop_event.set()
        else:
            self._stop_event.clear()

    def stop_recording(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        lease = None
        transcript = ""
        try:
            if self._runtime is not None:
                lease = self._runtime.acquire()
            transcript = self._run_session(lease)
        except Exception as exc:
            self.error.emit(f"Could not start dictation: {exc}")
        finally:
            try:
                if lease is not None:
                    lease.release()
            except Exception as exc:
                self.error.emit(f"Could not release local inference: {exc}")
            finally:
                self.finished.emit(transcript)

    def _run_session(self, lease) -> str:
        from voxd.core.archive import RecordingArchive
        from voxd.core.clipboard import ClipboardManager
        from voxd.core.recorder import AudioRecorder
        from voxd.core.typer import YdotoolTyper

        cfg = self.cfg
        server_url = lease.server_url if lease else cfg.gemma_server_url
        model = "gemma-e4b" if lease else cfg.gemma_model
        def create_transcriber():
            transcriber = GemmaAudioTranscriber(
                server_url=server_url,
                model=model,
                speech_preferences=self._speech_preferences,
                segment_seconds=cfg.gemma_segment_seconds,
                timeout=cfg.gemma_timeout,
                max_tokens=cfg.gemma_max_tokens,
                delete_input=not cfg.recording_archive_enabled,
            )
            return _ManagedTranscriber(transcriber, lease, cfg.gemma_timeout) if lease else transcriber

        recorder_factory = self._recorder_factory or AudioRecorder
        session = DictationSession(
            transcriber_factory=create_transcriber,
            recorder_factory=lambda transcriber: recorder_factory(
                chunk_seconds=cfg.record_chunk_seconds,
                input_device=cfg.audio_input_device,
                prefer_pulse=cfg.audio_prefer_pulse,
                segment_seconds=cfg.gemma_segment_seconds,
                segmenter_factory=transcriber.new_segmenter,
            ),
            clipboard_factory=ClipboardManager,
            typer_factory=lambda: YdotoolTyper(
                delay=cfg.typing_delay,
                word_delay=cfg.typing_word_delay,
                start_delay=cfg.typing_start_delay,
                cfg=cfg,
            ),
            archive_factory=lambda: RecordingArchive(
                max_bytes=cfg.recording_archive_max_mb * 1024 * 1024
            ),
            preserve_audio=cfg.recording_archive_enabled,
            transcription_metadata={
                "configured_model": model,
                "segment_seconds": cfg.gemma_segment_seconds,
                "server_url": server_url,
            },
            status_callback=self.status_changed.emit,
            error_callback=self.error.emit,
            stop_event=self._stop_event,
        )
        return session.run()
