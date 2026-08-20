from __future__ import annotations

import hashlib
from threading import Thread

from PyQt6.QtCore import QThread, pyqtSignal

from voxd.core.gemma_transcriber import GemmaAudioTranscriber, TranscriptionResult


class CoreProcessThread(QThread):
    """Own one record, transcribe, copy, and type cycle."""

    finished = pyqtSignal(str)
    status_changed = pyqtSignal(str)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.should_stop = False

    def stop_recording(self) -> None:
        self.should_stop = True

    def run(self) -> None:
        from voxd.core.archive import RecordingArchive
        from voxd.core.clipboard import ClipboardManager
        from voxd.core.recorder import AudioRecorder
        from voxd.core.typer import YdotoolTyper

        transcript = ""
        raw_transcript = ""
        recording_path = None
        dictation_error = None
        transcription_result: TranscriptionResult | None = None
        recorder = None
        live_thread = None
        live_state = {}
        try:
            transcriber = GemmaAudioTranscriber(
                server_url=self.cfg.gemma_server_url,
                model=self.cfg.gemma_model,
                segment_seconds=self.cfg.gemma_segment_seconds,
                overlap_seconds=self.cfg.gemma_segment_overlap_seconds,
                timeout=self.cfg.gemma_timeout,
                max_tokens=self.cfg.gemma_max_tokens,
                delete_input=not self.cfg.recording_archive_enabled,
            )
            recorder = AudioRecorder(
                chunk_seconds=self.cfg.record_chunk_seconds,
                input_device=self.cfg.audio_input_device,
                prefer_pulse=self.cfg.audio_prefer_pulse,
                segment_seconds=self.cfg.gemma_segment_seconds,
                segment_overlap_seconds=self.cfg.gemma_segment_overlap_seconds,
            )
            recorder.start_recording()
            live_thread = Thread(
                target=self._transcribe_live,
                args=(transcriber, recorder, live_state),
                name="voxd-gemma-live-transcription",
                daemon=True,
            )
            live_thread.start()
            while not self.should_stop:
                self.msleep(100)

            self.status_changed.emit("Transcribing")
            recording_path = recorder.stop_recording(
                preserve=self.cfg.recording_archive_enabled
            )
            if recording_path is None:
                raise RuntimeError("recorder produced no audio file")
            live_thread.join()
            if "result" in live_state:
                transcription_result = live_state["result"]
                transcriber.cleanup_input(recording_path)
            else:
                live_error = live_state.get("error", "live transcription ended unexpectedly")
                print(
                    f"[core] Live transcription unavailable; replaying after Stop: {live_error}",
                    flush=True,
                )
                transcription_result = transcriber.transcribe(recording_path)
            transcript = transcription_result.text
            raw_transcript = transcription_result.raw_transcript
            if not transcript:
                raise RuntimeError("E4B returned an empty transcript")

            # Clipboard is recovery state only; the normal insertion path is
            # always genuine ydotool input events.
            clipboard_ready = False
            try:
                ClipboardManager().copy(transcript)
                clipboard_ready = True
            except Exception as exc:
                # Clipboard is a fallback, not a prerequisite for real typing.
                print(f"[core] Could not copy recovery text: {exc}", flush=True)

            self.status_changed.emit("Typing")
            try:
                YdotoolTyper(
                    delay=self.cfg.typing_delay,
                    start_delay=self.cfg.typing_start_delay,
                    cfg=self.cfg,
                ).type(transcript)
            except Exception as exc:
                recovery = (
                    "full transcript remains on clipboard"
                    if clipboard_ready
                    else "clipboard recovery was also unavailable"
                )
                print(f"[core] Typing failed; {recovery}: {exc}", flush=True)
        except Exception as exc:
            dictation_error = str(exc)
            print(f"[core] Dictation failed: {exc}", flush=True)
            if recorder is not None and recorder.is_recording:
                try:
                    recording_path = recorder.stop_recording(preserve=True)
                except Exception:
                    recording_path = recorder.last_temp_file
            elif recorder is not None and recording_path is None:
                # stop_recording can preserve a partial WAV and then raise for
                # a capture/stream error before its return value is assigned.
                recording_path = recorder.last_temp_file
            if live_thread is not None:
                live_thread.join()
            transcript = ""
        finally:
            if (
                self.cfg.recording_archive_enabled
                and recording_path is not None
                and recording_path.exists()
            ):
                try:
                    RecordingArchive(
                        max_bytes=self.cfg.recording_archive_max_mb * 1024 * 1024
                    ).store(
                        recording_path,
                        {
                            "transcription": {
                                "configured_model": self.cfg.gemma_model,
                                "error": dictation_error,
                                "model": (
                                    transcription_result.resolved_model
                                    if transcription_result is not None
                                    else None
                                ),
                                "overlap_seconds": self.cfg.gemma_segment_overlap_seconds,
                                "prompt": transcriber.prompt,
                                "prompt_sha256": hashlib.sha256(
                                    transcriber.prompt.encode("utf-8")
                                ).hexdigest(),
                                "segments": raw_transcript.splitlines(),
                                "segment_seconds": self.cfg.gemma_segment_seconds,
                                "server_url": self.cfg.gemma_server_url,
                                "status": "complete" if transcript else "failed",
                                "system_fingerprint": (
                                    transcription_result.system_fingerprint
                                    if transcription_result is not None
                                    else None
                                ),
                                "text": transcript,
                            }
                        },
                    )
                except Exception as exc:
                    print(
                        f"[core] Could not finalize recording archive: {exc}",
                        flush=True,
                    )
            self.finished.emit(transcript)

    @staticmethod
    def _warmup_model(transcriber) -> None:
        try:
            transcriber.warmup()
        except Exception as exc:
            print(
                f"[core] Model warmup failed; continuing with normal transcription: {exc}",
                flush=True,
            )

    @classmethod
    def _transcribe_live(cls, transcriber, recorder, state) -> None:
        cls._warmup_model(transcriber)
        try:
            state["result"] = transcriber.transcribe_segments(recorder.iter_segments())
        except Exception as exc:
            state["error"] = exc
