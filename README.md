# VOXD

VOXD is a small Linux tray app for speech typing. It records until you stop,
transcribes through a local OpenAI-compatible Gemma E4B service, and inserts the
result as genuine keyboard input with `ydotool`.

It deliberately has one runtime path: tray → recorder → E4B → clipboard recovery
copy → `ydotool`. There is no Whisper model manager, post-processing layer,
paste-based insertion, or continuous VAD mode.

## What it supports

- Hindi in Latin/Roman script (Hinglish), English, and mixed speech
- punctuation inferred from pauses and intonation
- recordings of arbitrary practical length
- E4B's sub-30-second input limit through sequential 15-second segments with a
  1-second overlap
- complete text insertion into terminals and coding tools through real key events
- optional private FLAC recording history with transcript and model metadata
- failure recovery: source audio is kept if transcription fails, and VOXD tries
  to copy the final transcript before typing

Recording is streamed to bounded on-disk chunks, so speech duration is not capped
by memory. A single background worker transcribes each completed E4B segment in
order while recording continues. Stop closes and transcribes the final partial
segment, merges the overlap, and only then types the complete result.

Live segments use a bounded in-memory queue. If the endpoint fails or falls too
far behind, microphone capture and the full recording continue unaffected; after
Stop, VOXD replays the stitched WAV through the same sequential transcription
path. Archived FLAC audio therefore remains complete regardless of live decode.

## Requirements

- Linux with PipeWire/PulseAudio or another PortAudio input
- Python 3.9+
- `ydotool`, `ydotoold`, and a working user `ydotoold.service`
- `ffmpeg` when FLAC recording history is enabled (WAV is retained if unavailable)
- an OpenAI-compatible E4B endpoint, defaulting to `http://localhost:9292`

The endpoint must accept audio content at `/v1/chat/completions` using the
OpenAI-style `input_audio` message shape.

For the validated Radeon 780M Q8 setup, see [`runtime/igpu`](runtime/igpu). It
runs a separate MTP-enabled llama-swap endpoint with an 8K context and
five-minute idle unload; VOXD remains an ordinary OpenAI-compatible client.

## Source install

```bash
./setup.sh
```

Then start the tray:

```bash
.venv/bin/voxd --tray
```

Bind a desktop shortcut to toggle recording:

```bash
/absolute/path/to/voxd/.venv/bin/voxd --trigger-record
```

Start speaking after the tray shows Recording. Trigger again to stop; VOXD waits
for the complete transcription and then types it into the focused application.

## Configuration

The user config is `~/.config/voxd/config.yaml`. Important defaults:

```yaml
gemma_server_url: http://localhost:9292
gemma_model: gemma-e4b
gemma_segment_seconds: 15
gemma_segment_overlap_seconds: 1
gemma_timeout: 300
record_chunk_seconds: 300
recording_archive_enabled: false
recording_archive_max_mb: 5120
typing_delay: 0
typing_word_delay: 10
typing_start_delay: 0.15
```

On current ydotool builds, `typing_delay` controls the delay between characters
inside a word, while `typing_word_delay` adds a short pause between word runs.
VOXD sends each bounded text chunk in one process, so word pacing does not add a
process launch per word. Older ydotool builds retain the compatible stdin typing
path and ignore `typing_word_delay`.

`record_chunk_seconds` controls on-disk chunk rotation, not maximum speech length.
The E4B service should stay warm for low latency; VOXD does not own or restart it.

Enable private benchmark history explicitly:

```bash
voxd --archive-recordings true
```

Audio is archived under `~/.local/share/voxd/recordings/` as FLAC with a JSON
sidecar containing the transcript, raw segments, prompt, and model metadata.
The archive is private to the current user and capped by
`recording_archive_max_mb`. Compression failure retains the source WAV instead.

Useful commands:

```bash
voxd --diagnose
voxd --autostart true
voxd --autostart false
voxd --archive-recordings true
voxd --archive-recordings false
voxd --version
```

If typing fails, verify `ydotool` and its socket:

```bash
systemctl --user status ydotoold.service
voxd --diagnose
```
