# ThoughtLoud

**Think out loud. In your own words.**

ThoughtLoud is a Linux app for long, natural dictation: explaining an idea, working
through a coding problem, or speaking a detailed prompt to an AI tool. Take your
time and speak in comfortable native or code-switched speech. Press your shortcut
to start, press it again to stop, and the complete transcript is typed into the
focused application.

## Linux package candidates

Linux is the release target. The current packages target **x86_64 Linux**, using
Ubuntu 24.04 as the build baseline, and support Vulkan GPU acceleration. The
native runtime selects a compatible CPU backend at startup, using AVX2 or newer
optimizations when supported. AVX2 is not a hard requirement; the bundled Qt
requires an SSE4.2/POPCNT-capable CPU (the x86-64-v2 baseline). `.deb` and `.rpm`
recipes bundle Python, Qt, transcription
dependencies, and the native inference runtime. Users do not need to install
Python or manage a virtualenv.

The candidate has been exercised on Ubuntu 24.04 GNOME Wayland in QEMU/KVM,
including a fresh model download, a desktop shortcut and dictation into an editor.
Fedora 44 RPM installation/startup and a Radeon 780M Vulkan audio run have separate
smoke evidence. Package installation and user-data preservation were also
verified on Ubuntu and Fedora.
Physical microphone/headset and KDE portal checks remain open.
See the current [release evidence and remaining checks](docs/linux-release.md).

Install a locally built candidate with your package manager:

```bash
# Ubuntu candidate
sudo apt install ./thoughtloud_*_amd64.deb

# Fedora candidate
sudo dnf install ./thoughtloud-*.x86_64.rpm
```

Open **ThoughtLoud** from the application menu. It opens a small setup/settings window;
normal dictation runs from the tray or your shortcut. The settings window is also
available with `thoughtloud --settings`, including on desktops without a tray.
First-run setup prepares the bundled typing service in your desktop session;
its status and retry control are shown in the window.

1. **Run the transcription model on this computer** is selected for fresh installs. Choose CPU or Vulkan,
   and download the local models (about **9.2 GB**). Download progress,
   cancellation, retry, and file verification are built in. An existing
   compatible Gemma endpoint can be used instead.
2. Optionally describe **How do you like to talk?** For example: “I mix Marathi
   and English, often using software engineering terms.” Leave it empty for a
   neutral language preference.
3. Use **Configure hotkey with desktop**, then save settings. When the desktop
   does not provide the shortcut portal, the window explains how to create a
   custom keyboard shortcut and shows its exact command.
4. Focus an editor, terminal, or chat. Press the shortcut, start speaking once
   recording begins, and press it again to stop. Keep the intended destination
   focused until insertion finishes.

The local runtime uses Gemma E4B Q8 weights with an F16 audio projector. CPU and
Vulkan are separate profiles. The current managed package omits the MTP assistant;
the [validated iGPU runtime](runtime/igpu/README.md) includes it and enables MTP.
These are different runtime configurations. Vulkan needs a compatible host driver. Allow disk space for the download and recordings,
and enough memory for the model and inference. The CPU desktop test used a
14 GiB VM; the native server peaked at about 9.2 GiB RSS, excluding the desktop
and application. This is an observed working setup, not a minimum-memory claim.
Accelerator changes take effect
after restarting the app.

## Speech and microphone behavior

- Speech preferences are context for transcription, rather than translation or
  rewriting instructions. Output remains **printable ASCII**, with
  transliteration where needed. Language quality needs examples; configurable
  preferences do not imply every language is validated.
- Each recording uses the current system-default microphone. The app does not
  unmute it or change its volume. A confirmed muted input blocks recording and
  shows an alert to unmute and retry. When mute status cannot be determined,
  recording proceeds; silence alone is not treated as proof of mute.
- Long recordings are decoded in order while you speak. If live decoding fails,
  the complete recording is replayed after Stop. Only the complete result is
  inserted.
- Final text is copied to the clipboard for recovery and inserted through
  `ydotool` key events. Clipboard failure does not block typing. Empty speech
  produces no insertion.

## Runtime and troubleshooting

The package includes its own `ydotool`/`ydotoold` helpers. The host supplies a
systemd user session, Linux audio stack, clipboard helpers, PortAudio, and Qt's
system libraries; dependencies are listed in [packaging](packaging/README.md).
Text insertion needs the user daemon and active-session access to `/dev/uinput`.
Optional FLAC history uses `ffmpeg`;
without it, the recording remains WAV.

For local mode, the app owns a loopback server, keeps it available through the
complete dictation, and unloads it after five idle minutes. Downloads happen
only through setup. With an existing endpoint, the app uses that service without
starting, stopping, or replacing it. The [validated iGPU/MTP setup](runtime/igpu)
remains available for existing installations.

Useful diagnostics:

```bash
thoughtloud --diagnose
systemctl --user status voxd-ydotoold.service
thoughtloud --settings
```

Settings are stored in
`~/.config/voxd/config.yaml` and local models in
`~/.local/share/voxd/models/` (or their XDG equivalents). Local runtime errors
include the path to `managed-runtime.log` in that model directory. The settings
window can check microphone status and send synthetic silence to an existing
endpoint without recording or typing microphone audio.

Recording history is off by default. Enable it with
`thoughtloud --archive-recordings true`. History is private to the current user and
capped by `recording_archive_max_mb` (default 5120). Audio and transcript metadata
are stored under `~/.local/share/voxd/recordings/`; failed compression retains WAV.

## Develop and contribute

For a source checkout, use Python 3.12, a compatible modern ydotool client/daemon
(the package pins 1.0.4), and the Linux prerequisites above:

```bash
./setup.sh
.venv/bin/thoughtloud --settings
```

Fresh source installs also select local mode. Build the
[native Linux runtime](runtime/linux/README.md) and set `VOXD_LLAMA_RUNTIME` to
its output directory, or disable local mode in settings and supply an existing
compatible Gemma audio endpoint.

The [architecture guide](docs/architecture.md) documents the UI-independent
dictation session, injectable platform operations, Gemma backend, and preserved
transcription behaviors. Start with the
[file-transcription example](examples/transcribe_file.py) for backend use without Qt.

**Community macOS, Windows, and Android ports and builds are welcome.** Include
the platform's microphone, shortcut, text-insertion, permissions, lifecycle and
runtime packaging, plus installation instructions and checks on that platform.
Linux remains the project's official release and validation scope.

Build instructions are in [packaging/README.md](packaging/README.md). The
[distribution guide](docs/licensing.md) explains the desktop bundle licenses and
matching source downloads. See
[LICENSE](LICENSE), [third-party notices](THIRD_PARTY_NOTICES.md),
[artwork terms](ASSETS_LICENSE), and [upstream trademark policy](TRADEMARKS.md)
before redistributing a derivative.
