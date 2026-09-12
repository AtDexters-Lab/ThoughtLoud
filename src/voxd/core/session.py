from __future__ import annotations

import hashlib
import json
from threading import Event, Thread
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from voxd.core.gemma_transcriber import TranscriptionResult


class DictationSession:
    """Run one dictation using injected platform and transcription operations.

    This module imports no UI, audio-driver, or OS-integration packages. Factories
    create one transcriber, recorder, clipboard, typer, and optional archive per
    session. The recorder factory receives the transcriber so the adapter can
    share its segmentation implementation. A recorder must close its segment
    iterator when stopped, including when capture fails.

    ``run`` blocks until ``stop_recording`` is called, finishes the live decode
    (or replays the complete WAV), then copies and types only the final result.
    Call it on a thread chosen by the platform adapter. Factories and status
    callbacks run on that thread; audio decode runs on the internal live thread.
    """

    def __init__(
        self,
        *,
        transcriber_factory: Callable,
        recorder_factory: Callable,
        clipboard_factory: Callable,
        typer_factory: Callable,
        archive_factory: Callable | None = None,
        preserve_audio: bool = False,
        transcription_metadata: dict | None = None,
        status_callback: Callable[[str], None] | None = None,
        error_callback: Callable[[str], None] | None = None,
        stop_event: Event | None = None,
    ):
        self.transcriber_factory = transcriber_factory
        self.recorder_factory = recorder_factory
        self.clipboard_factory = clipboard_factory
        self.typer_factory = typer_factory
        self.archive_factory = archive_factory
        self.preserve_audio = preserve_audio
        self.transcription_metadata = dict(transcription_metadata or {})
        self.status_callback = status_callback or (lambda _status: None)
        self.error_callback = error_callback or (lambda _error: None)
        self._stop_event = stop_event if stop_event is not None else Event()

    def stop_recording(self) -> None:
        self._stop_event.set()

    def run(self) -> str:
        transcript = ""
        raw_transcript = ""
        recording_path = None
        dictation_error = None
        transcription_result: TranscriptionResult | None = None
        transcription_source = None
        transcriber = None
        recorder = None
        live_thread = None
        live_state = {}
        try:
            transcriber = self.transcriber_factory()
            recorder = self.recorder_factory(transcriber)
            recorder.start_recording()
            live_thread = Thread(
                target=self._transcribe_live,
                args=(transcriber, recorder, live_state),
                name="voxd-live-transcription",
                daemon=True,
            )
            live_thread.start()
            self._stop_event.wait()

            self.status_callback("Transcribing")
            recording_path = recorder.stop_recording(
                preserve=self.preserve_audio
            )
            if recording_path is None:
                raise RuntimeError("recorder produced no audio file")
            live_thread.join()
            if "result" in live_state:
                transcription_result = live_state["result"]
                transcription_source = "live"
                transcriber.cleanup_input(recording_path)
            else:
                live_error = live_state.get("error", "live transcription ended unexpectedly")
                print(
                    f"[core] Live transcription unavailable; replaying after Stop: {live_error}",
                    flush=True,
                )
                transcription_result = transcriber.transcribe(recording_path)
                transcription_source = "replay"
            transcript = transcription_result.text
            raw_transcript = transcription_result.raw_transcript
            if transcript:
                # Clipboard is recovery state only; the normal insertion path is
                # always the platform adapter's normal typing operation.
                clipboard_ready = False
                try:
                    self.clipboard_factory().copy(transcript)
                    clipboard_ready = True
                except Exception as exc:
                    # Clipboard is a fallback, not a prerequisite for real typing.
                    print(f"[core] Could not copy recovery text: {exc}", flush=True)

                self.status_callback("Typing")
                try:
                    self.typer_factory().type(transcript)
                except Exception as exc:
                    recovery = (
                        "full transcript remains on clipboard"
                        if clipboard_ready
                        else "clipboard recovery was also unavailable"
                    )
                    print(f"[core] Typing failed; {recovery}: {exc}", flush=True)
                    self.error_callback(f"Typing failed; {recovery}: {exc}")
            else:
                print("[core] No intelligible speech detected", flush=True)
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
            recovery = f" Recording retained at {recording_path}." if recording_path else ""
            self.error_callback(f"Dictation failed: {exc}.{recovery}")
        finally:
            if (
                self.preserve_audio
                and transcriber is not None
                and self.archive_factory is not None
                and recording_path is not None
                and recording_path.exists()
            ):
                try:
                    protocol = transcriber.protocol_metadata()
                    protocol_sha256 = hashlib.sha256(
                        json.dumps(
                            protocol,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                    ).hexdigest()
                    self.archive_factory().store(
                        recording_path,
                        {
                            "transcription": {
                                **self.transcription_metadata,
                                "error": dictation_error,
                                "model": (
                                    transcription_result.resolved_model
                                    if transcription_result is not None
                                    else None
                                ),
                                "prompt": transcriber.prompt,
                                "prompt_sha256": hashlib.sha256(
                                    transcriber.prompt.encode("utf-8")
                                ).hexdigest(),
                                "protocol": protocol,
                                "protocol_sha256": protocol_sha256,
                                "segments": raw_transcript.splitlines(),
                                "segment_modes": (
                                    list(transcription_result.segment_modes)
                                    if transcription_result is not None
                                    else []
                                ),
                                "status": (
                                    "failed"
                                    if dictation_error
                                    else "complete"
                                    if transcript
                                    else "no_speech"
                                ),
                                "source": transcription_source,
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
        return transcript

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
