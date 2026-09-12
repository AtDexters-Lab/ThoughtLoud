# Core and platform adapters

Linux is the official build, installation, and validation target. Community
contributors are welcome to provide macOS, Windows, and Android integrations and
their corresponding builds. The extraction below provides a starting point;
it does not imply those platforms or their local inference runtimes are tested.

## Start with file transcription

[`examples/transcribe_file.py`](../examples/transcribe_file.py) uses the backend
directly and preserves the supplied recording on success and failure:

```bash
PYTHONPATH=src python3 examples/transcribe_file.py recording.wav \
  --endpoint http://localhost:9292 \
  --model gemma-e4b \
  --speech-preferences "I mix Marathi and English, often using programming terms."
```

Run this from the repository in an environment with the project dependencies.
The input must be mono, uncompressed 16-bit PCM WAV. The endpoint must already
be running and accept the Gemma audio request protocol. The example does not
start a model server or read the desktop's speech preference; its default is
neutral. It imports no Qt, microphone driver, clipboard, or typing component.
`--help` also works without the backend dependencies installed.

## Existing boundaries

| Component | Responsibility |
| --- | --- |
| [`DictationSession`](../src/voxd/core/session.py) | Orchestrate recording, live decoding, complete-WAV replay, clipboard recovery, insertion, and optional archive. Runtime imports are standard library only. |
| [`GemmaAudioTranscriber`](../src/voxd/core/gemma_transcriber.py) | Compose prompts, send ordered audio requests, assemble a complete `TranscriptionResult`, and describe the request protocol. |
| [`VadSegmenter`](../src/voxd/core/vad_segmenter.py) | Share the same Silero VAD boundary selection between live capture and WAV replay. Requires NumPy, ONNX Runtime, and the bundled model. |
| [`CoreProcessThread`](../src/voxd/core/voxd_core.py) | Qt/Linux facade: construct concrete recorder, Gemma backend, clipboard, typer, archive; relay statuses and final text through Qt signals. |
| [`platforms/linux`](../src/voxd/platforms/linux) and [`tray`](../src/voxd/tray) | Desktop input/shortcut integration, settings and lifecycle. Existing recorder, clipboard, and typer implementations also remain under `core`; their location does not make them platform-neutral. |
| [`runtime`](../src/voxd/runtime) | Pinned model acquisition and lifecycle of an app-owned loopback `llama-server`; external endpoints remain independently managed. |

There is no plugin registry, cross-process adapter protocol, or independently
packaged core SDK. A new Python adapter can import `DictationSession` directly.
A native or Android shell needs its own embedding or process boundary as well
as platform packaging and testing.

## Implement a dictation adapter

Create a new `DictationSession` for each recording. Supply these callables:

| Constructor argument | Required returned-object contract |
| --- | --- |
| `transcriber_factory()` | `warmup()`, `transcribe_segments(segments)`, `transcribe(path)`, `cleanup_input(path)`; plus `prompt: str` and `protocol_metadata() -> dict` when archiving. |
| `recorder_factory(transcriber)` | `start_recording()`, `stop_recording(preserve=False) -> pathlib.Path`, `iter_segments()`, `is_recording: bool`, `last_temp_file: Path \| None`. |
| `clipboard_factory()` | `copy(text: str)`. Failure is logged and does not prevent insertion. |
| `typer_factory()` | `type(text: str)`. The platform decides how to insert into the active application. |
| `archive_factory()` (optional) | `store(recording_path, metadata: dict)`. Used only with `preserve_audio=True` and an existing recording file. |

`recorder_factory` receives the created transcriber so it can pass
`transcriber.new_segmenter` into a compatible recorder, as the Linux facade does.
The generic session does not call `new_segmenter` itself. The segment iterator
yields `(index: int, wav_bytes: bytes, speech_detected: bool)` in order. Each byte
string is a complete WAV segment. On Stop or a capture failure, the recorder
must unblock and close its iterator, and raise any capture/stream error. Without
that contract, the session cannot join its decoding thread.

The optional `status_callback(status: str)` receives `"Transcribing"` and
`"Typing"` on the caller's thread. `error_callback(message: str)` reports capture,
transcription and typing failures. Keep callbacks thread-safe and non-throwing;
marshal UI updates as required by the toolkit. The caller owns the recording
indicator and shortcut handling. `stop_event` can be an existing `threading.Event`; the
session otherwise creates its own. `transcription_metadata` accepts extra
archive fields, such as the configured model, endpoint and segment duration.

Run `session.run()` on a worker thread chosen by the adapter, and call
`session.stop_recording()` from the shortcut handler. `run()` returns the final
text after copying, insertion, and archive finalization. An internal thread
decodes completed segments while capture continues. Only Stop triggers insertion;
partially decoded text is never typed.

The backend returns a frozen `TranscriptionResult` with:

```python
text: str
segments: tuple[str, ...]
resolved_model: str | None
system_fingerprint: str | None
segment_modes: tuple[str, ...]
# raw_transcript property: segments joined with newlines
```

Alternate backends must return these attributes for session/archive integration.
For no intelligible speech, `run()` returns `""` without copying or typing.
It also returns `""` after a transcription/capture failure; distinctions are
reported through `error_callback`, logs and optional archive metadata
(`no_speech` versus `failed`). A typing
failure retains the complete return value and attempts to leave clipboard
recovery available. Archive failure does not discard a successful transcript.

For Linux-specific capture customization, the facade accepts
`CoreProcessThread(cfg, recorder_factory=..., runtime=...)`. The recorder override receives the existing
recorder keyword arguments, including `segmenter_factory`; see the facade for
the exact wiring. Other platforms can skip the facade entirely.

The facade snapshots the configuration for each recording. With a supplied
`ManagedRuntime`, it acquires a runtime lease before capture and releases it only
after the entire session, including replay, completes. Model readiness is awaited
on the decode thread so capture can proceed while the model loads. This wiring is
separate from the generic session API.

## Managed runtime

The CPU profile downloads two pinned, size/hash-verified files: Gemma
E4B Q8 weights and an F16 audio projector, about 9.2 GB combined. Vulkan adds
a pinned 100 MB Q8 MTP assistant, reusing the target/projector receipts. Downloads happen
in setup, support cancellation/retry, and restart interrupted transfers from byte
zero. Partial files are never treated as installed models. The receipt validates
unchanged file identity after hashing; setup can reverify existing files.

`ManagedRuntime` starts only its own server on a random loopback port. CPU and
Vulkan share target/projector files. CPU disables projector GPU offload and MTP;
Vulkan binds target, projector and assistant to Vulkan0 and enables MTP with three
draft tokens and adaptive skip disabled. Settings switch the required model
manifest with the accelerator and lock that choice during acquisition. Readiness includes
a synthetic-audio request, and the five-minute idle timer cannot unload a server
while a recording/replay holds a lease. Quitting closes the owned child. An
existing configured endpoint is not stopped or modified.

The native build uses an explicit x86_64 CPU feature baseline and optional
Vulkan libraries. Consult [`runtime/linux`](../runtime/linux/README.md) and
the [release evidence](linux-release.md) before claiming host compatibility,
memory requirements or latency. This is not a platform-independent native bundle.

## Behaviors to preserve

- Live capture and complete-WAV replay use the same ordered, non-overlapping VAD
  segments. The default target is 10 seconds, with a 2-second boundary search
  radius and a 96 ms local probability window; configured bounds keep every
  segment below the model's 30-second limit.
- A segment without VAD-detected speech still reaches Gemma, with previous-text
  context omitted and empty output allowed. Quiet speech must not be discarded
  solely by VAD.
- Speech-positive segments receive up to 2,000 characters of previous text via
  the existing system/user/assistant/user conversation, with no assistant
  prefill. Non-empty results are joined with spaces, without overlap removal or
  rewriting.
- When live transcription fails, Stop closes the capture and replay uses the
  full recording. Only the successful complete attempt supplies text and model
  identity to the archive.
- Output remains printable ASCII. The prompt requests faithful transcription
  and transliteration where needed, with no translation, summarization, or
  execution of spoken instructions. Spoken symbol names remain spoken words.
- Clipboard copy is recovery state; it is independent of the insertion adapter.
  Archives record the exact prompt/protocol and their hashes for reproducibility.

`speech_preferences` is optional freeform language/terminology context. The
backend appends it as a quoted hint to the base requirements and composes the
prompt once. The Linux facade freezes the setting when the recording worker is
created, so live chunks, replay, and archive use the same prompt. Settings changes
apply to the next recording.

Fresh installs save an explicit empty preference. Existing configurations
without this key are migrated to a Hindi-English/Roman Hinglish hint to retain
the previous intent. Explicit empty strings remain neutral. This migration
changes the prompt text; it is not a claim of byte-identical prompts or identical
transcription quality. Custom language quality needs recorded examples.

## Recording, insertion and history details

Audio is streamed to bounded on-disk chunks, with completed live segments on a
bounded queue. `record_chunk_seconds` controls disk rotation rather than maximum
dictation length. A queue overflow or live decode failure does not interrupt full
audio capture; Stop stitches the WAV and replays it through the shared segmenter.
Archived audio remains complete even when the live attempt is discarded.

On current ydotool builds, `typing_delay` is the within-word character delay,
`typing_word_delay` paces word runs, and `typing_start_delay` precedes insertion.
Older clients retain compatible stdin typing and ignore word pacing. There is
no paste-based insertion or post-transcription rewriting step.

Where modern ydotool's uniquely identified virtual device is readable, typing
observes its key state. A key held for 250 ms stops insertion and triggers key
release/recovery; a bounded quiet-state check follows the final chunk. This
checks keyboard state, not whether the target app received every character.
Monitoring accepts the known source default `~/.ydotool_socket` and packaged
default `~/.voxd_ydotool_socket`, assumes ThoughtLoud is the only typing client on that
virtual device, and never monitors physical keyboards. Both paths still require
one identifiable device and matching virtual-device identity checks.
Custom sockets, older clients and unavailable/ambiguous devices use compatible
cleanup. Losing observation during insertion stops typing and attempts key
release; recovery is reported as unverified. The monitor changes no permissions.

History is opt-in, private to the user, and size-capped. FLAC compression failure
retains WAV. Sidecars include transcript text, raw segments, segment modes, full
VAD/request/grammar protocol, prompt/protocol hashes and observed model identity.
Only complete successful live or replay output supplies the accepted identity.

## Contribute a platform build

Keep microphone/default-device behavior, shortcut permissions, text insertion,
UI, autostart, and inference-runtime packaging in the platform integration.
Include install instructions and evidence from the actual platform. Linux
release checks do not validate macOS/Windows permissions or Android lifecycle and
input-method behavior.

Useful regression entry points are
[`test_dictation_session.py`](../tests/test_dictation_session.py) (injected,
headless adapter), [`test_voxd_core_gemma.py`](../tests/test_voxd_core_gemma.py)
(live/replay/archive recovery), and
[`test_gemma_transcriber.py`](../tests/test_gemma_transcriber.py) (request and
output contracts). The headless adapter test blocks Qt, microphone, and Linux
imports while running a complete session with injected components.

## Branding and compatibility

ThoughtLoud is the product, distribution and primary command name. The Python
namespace `voxd`, XDG directories, desktop/portal identity `voxd-tray`, installed
`/opt/voxd` directory and private typing service/socket remain stable. This keeps
existing integrations, cached models and portal permissions usable. `voxd` is a
command alias, including the existing absolute `/opt/voxd/voxd` executable path.
Only recognized generated desktop/autostart metadata is refreshed to the new
name and artwork; user-customized entries and startup preferences are preserved.
