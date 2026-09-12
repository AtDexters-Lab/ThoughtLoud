# Linux release scope and evidence

The intended release is a Linux application for long, comfortable native and
code-switched dictation. A fresh user should install a package, configure a
shortcut and optional speech preference, download/use the model, and dictate
into an application without a developer environment or manual Python setup.

This page records the implemented candidate, its actual validation and remaining
publication/support boundaries. The work is local and uncommitted; no release has
been published.

The latest candidate includes the workstation repairs and portable CPU dispatch
described below and passes **329 tests**. Its rebuilt packages are recorded in
the portable CPU section. Earlier package and VM evidence applies to the
identified earlier artifacts; those packages and their matching source were
retained separately before rebuilding.

## Accepted scope

- Linux is the official build and validation target. Community macOS, Windows
  and Android adapters/builds are invited, with their own platform validation.
- Preserve the existing tuned long-dictation behavior while extracting a
  reusable session with injected microphone, transcription, clipboard, insertion
  and archive operations. Use bundled Python for this release.
- Provide a small setup/settings window, system-default input, configurable
  hotkeys, optional speech preferences and user-controlled autostart.
- Respect system microphone mute/volume. A confirmed muted source at the start
  shortcut produces a visible warning; unknown status remains distinguishable.
- New speech preferences are neutral by default; existing configs missing the
  key receive the legacy Hindi-English hint. Output remains printable ASCII.
- Offer a managed Gemma E4B Q8/F16 profile with CPU or Vulkan, a verified model
  download, owned runtime lifecycle and idle unload, and preserve the existing
  external-endpoint option. Fresh configs select local inference; existing
  configs missing the new setting retain their endpoint. The implemented managed
  candidate omits the MTP assistant as a packaging simplification; this is not
  an established technical requirement or an accepted decision to disable MTP
  in the eventual default profile. The existing iGPU profile retains MTP.
- Target native amd64 `.deb` and `.rpm` packages, with an Ubuntu 24.04 candidate
  build baseline. Select optimized native CPU backends opportunistically rather
  than requiring AVX2. The bundled Qt retains its separate x86-64-v2 baseline.
- ThoughtLoud is the approved product name, with original MIT SVG/PNG artwork.
  Retain the `voxd` Python/data/desktop/private-service identities and command
  aliases for compatibility; no model or config directory migration is required.
  Keep upstream attribution and exclude inherited logos from product packages.

## Candidate implementation

| Area | Implemented surface | Validation status and remaining evidence |
| --- | --- | --- |
| Dictation core | Headless session, injectable platform operations, frozen session configuration, live decode/replay/archive recovery | Two public Hindi-English paired clips passed below; spontaneous long-speech quality remains unmeasured |
| Settings | Speaking preference, runtime profile/download, endpoint check, hotkey guidance, autostart and visible controls without a tray | Ubuntu fresh-user walkthrough completed; supported-portal consent remains untested |
| Microphone | Per-start default-source lookup, exact-source capture where available, positive mute alert, no automatic volume/unmute | Real headset/default changes, mute/unmute, missing helpers and capture failure |
| Shortcuts | XDG portal connection with application registration and guided desktop fallback | GNOME manual fallback and actual saved hotkey passed; supported portal consent/binding/restart remains untested |
| Local model | Pinned Q8 target and F16 projector, size/hash checks, cancellation and retry | Ubuntu full download, cancel/retry and interrupted-transfer recovery completed; low-space preflight covered by tests |
| Local runtime | App-owned loopback child, readiness check with synthetic audio, per-dictation lifetime, five-minute idle unload | CPU/Vulkan public audio and managed CPU VM dictation completed; physical microphone and broader hardware checks remain |
| Packaging | Python 3.12 lock, PyInstaller onedir, pinned native recipe and nfpm 2.47.0 recipes | ThoughtLoud packages and matching source artifacts built and verified; Ubuntu/Fedora replacement tested; public release remains separate |

Start at login uses an XDG desktop entry in
`$XDG_CONFIG_HOME/autostart/voxd-tray.desktop` (normally
`~/.config/autostart/voxd-tray.desktop`), so the desktop session supplies its
display environment. Every Settings Save reapplies this preference and migrates
an enabled legacy `voxd-tray.service` after successfully updating the desktop
entry. Migration only disables future service starts; it does not stop the app
or typing daemon. A failed migration is reported as incomplete in Settings.
The packaged tray service remains available for manual compatibility use.

## Validation boundaries

Unit and integration tests cover injected session operations, prompt/config
migration, VAD/request behavior, replay/recovery, microphone probing, capture
tail handling, portal responses and settings flows. These establish code behavior
under their supplied fixtures. Mocked devices/portals do not prove a desktop's
permissions, focus behavior or audio routing.

Frozen `--version`/`--help` checks and package construction establish that
artifacts can be produced and start in the build environment. Synthetic-silence
endpoint checks establish acceptance of the audio protocol. Neither proves
speech quality or that a focused editor received the intended text.

The candidate is an uncommitted working tree based on
`03d4cce661fa36f66f91a5032a9569f55e452623`. The builder is Ubuntu 24.04,
Python 3.12, with the hash-locked Python dependencies and native recipes in
`packaging/` and `runtime/linux/`. The following identities record the validated pre-rebrand baseline from
2026-09-11. ThoughtLoud rebrand artifact evidence is recorded separately below. The full source check
`QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest -q`
passed **266 tests in 6.55 seconds** on 2026-09-11; `git diff --check` was clean.

| Artifact | SHA-256 |
| --- | --- |
| `voxd_1.4.0-1_amd64.deb` | `2b7759c339b2e18c4bd0f383298fe2ed2e4f5fa6f4ad5ea02567b768e2e39719` |
| `voxd-1.4.0-1.x86_64.rpm` | `a3623b3d0989cfca61dff032ba633c9eb5f0b0b17bbf80748081073126c48d2c` |
| Bundled `voxd` executable | `971e529997baff347e754fe81b1fd52910f956cfc25323428266149d226f752b` |

Native source revision is `0a635dcd92ba66c75fccfef91c3e106f4668f367` with
`runtime/igpu/atomic-mtp-audio.patch`; native provenance and exact source accompany
the binary. The Q8 model digest is
`a2232a649523c36bf530f1dc3614eb8c800645c4227390381c8b05d4d6eee05a`,
and the F16 projector digest is
`ddf46c21d7078e95338cfc22306b19b276a29a5ad089023449dd54d4b6170a51`.
Both use revision `653803f092503c04a65164346f3208a36e707693`.
Application source is also assembled locally as `voxd-1.4.0-source.tar.gz`;
that archive alone is not the complete corresponding source of every bundled
dependency.

### Development-host CPU and Vulkan exercise

The managed runtime was exercised with the newly built native AVX2 binary,
four CPU threads and MTP disabled. Recorded results in the local build artifact
`build/validation/runtime-result.json`:

| Check | Observation |
| --- | --- |
| Audio readiness after process start | 3.69 seconds, with warm filesystem cache |
| Public 11-second JFK speech sample | 17.93 seconds to transcribe |
| Same sample repeated to 33 seconds | 42.97 seconds, two production VAD segments, all three repetitions preserved |
| Idle shutdown | Owned child stopped using a one-second test idle threshold |
| Bundled Vulkan runtime, Radeon 780M/RADV | Same short/long text as CPU, byte-for-byte; 3.99 / 9.81 seconds respectively |
| Vulkan audio readiness | 25.91 seconds; the disposable test had no writable shader cache |

The Vulkan run used the actual bundled native binary with the host driver and
render device mounted into an isolated container. The CPU run used the same
native build before bundle assembly. Both were restricted to four CPU threads;
cached model files were hash-verified and all requests used public speech.
These observations do not measure microphone capture, typing, a cold filesystem
cache, VM performance, or speech quality across languages. GPU results apply
only to this tested Radeon/Mesa combination. The production idle interval remains
five minutes.

### Speech preference comparison

Two public Hindi-English technical-speech clips from MUCS/OpenSLR104 were run
through the original prompt, the migrated Hindi-English preference, and a custom
Hindi-English/software preference. The same CPU runtime and production 10-second
VAD target were used for all six runs. The 12.55-second clip produced identical
text in all three runs, preserving Ubuntu/GCC terminology and version numbers.
On the 27.55-second clip, the custom preference matched the original text; the
migrated preference changed one Roman spelling (`main`/`mein`) and one comma.
All runs retained ASCII output and ordered segment assembly.

Local evidence is `build/validation/mucs/paired-results.json`, with audio hashes,
source metadata and exact prompt hashes alongside it. Samples are test rows 79
and 146 of the [MUCS-Hinglish mirror](https://huggingface.co/datasets/dianavdavidson/MUCS-Hinglish).
Retain the upstream [OpenSLR104 CC BY-SA 4.0 terms](https://www.openslr.org/104/),
despite the mirror's different license label. Row 146's reference appears to
span different boundaries from its audio, so it was used only for paired behavior;
no accuracy score was calculated. These are short instructional clips, not a
benchmark of spontaneous long speech or all supported languages.

### Ubuntu desktop walkthrough

The test environment was an isolated QEMU/KVM guest with four vCPUs, 14 GiB RAM,
a 20 GB virtual disk, GNOME 46 on Wayland and an ordinary active-seat user. The
Ubuntu 24.04.4 cloud image digest was
`d0fe84bb5f80853425fa6be28e2c106f30104c3cfe8611933f2e65c9b63f0e30`.
The desktop image started without app config, model cache or a Python development
environment. QEMU keyboard events exercised the GUI; a virtual Pulse source
supplied repeatable public audio. This does not substitute for a physical headset.

Observed on the installed candidate:

- Package dependencies resolved and the application-menu/setup route worked.
  First-run setup prepared the bundled typing daemon and active-seat input access
  without adding the user to a broad input-device group.
- The settings download was cancelled after about 0.83 GB; its partial file was
  removed. Retry and a later interrupted-transfer retry completed both model files
  with the declared sizes and SHA-256 hashes. No model cache was injected.
- GNOME 46 did not expose the shortcut portal. The displayed fallback command was
  assigned to Ctrl+Alt+Space in GNOME's custom shortcut UI, then exercised as an
  actual global hotkey.
- A muted default source produced a visible warning and started neither capture
  nor the model. After reboot, changing to a second default source at 37% volume
  produced the same warning; the app left its mute/volume unchanged.
- Upgrading the final `.deb` preserved config SHA-256
  `b894647cc15d88310a1329cacc76bf6fc2a2904394754e2866e1e3184c844df9`
  and the downloaded models. The installed executable matched the final bundle.
- Saving the unchanged Start at login setting wrote the XDG entry and disabled
  the legacy tray unit while keeping the running app and typing service alive.
  A subsequent reboot launched one app in the GNOME application scope, with the
  legacy unit disabled and no early Qt crash. The hotkey worked after login.

An earlier candidate captured only 31.744 seconds of the 33-second fixture because
`parec` used its default two-second record fragment. A separate no-model control
confirmed the buffer effect. The final adapter requests 50 ms and retains stdout
EOF draining before closing the WAV. This is a requested latency, not a guarantee
for every physical audio device.

The final post-reboot test used 45.55 seconds of public English and Hindi-English
speech: the three JFK repetitions followed by MUCS test row 79, without added tail
silence. The WAV contained 46.0 seconds including capture lead-in/out. Source
alignment retained the full fixture; the last half-second correlated at 0.983
with the source, with the same 0.213-second offset as its beginning. Five decoded
segments preserved all three English repetitions and the Hindi-English technical
tail including Ubuntu 11.10 and GCC 4.6.1. The 440-character ASCII result matched
the actual saved editor text and clipboard. Archive metadata reported complete.
Native server peak RSS was 9,597,060 KiB in this run. Evidence:
`ubuntu-final-result.json`, `ubuntu-final-alignment.json`,
`ubuntu-final-mixed.json`/`.wav`, `ubuntu-final-editor.txt`,
`ubuntu-final-clipboard.txt` and `build/vm/final-mixed-dictation.png`.
This proves the tested virtual-input flow, not spontaneous speech accuracy.

Local evidence is under `build/validation/ubuntu-*` and `build/vm/*.png`. The
pre-fix `ubuntu-dictation.json`/`ubuntu-captured.wav` are retained as diagnostic
evidence and must not be used as proof of the final tail fix. The task VM is
reusable; its scripts and ephemeral SSH key are local ignored test artifacts.

### Fedora package smoke

The final RPM `a3623b3d0989cfca61dff032ba633c9eb5f0b0b17bbf80748081073126c48d2c`
installed in the official Fedora 44 container with its 56 dependencies. It passed
package-file verification, frozen CLI/native-runtime startup, nonroot offscreen
settings, existing-instance IPC, and removal preserving config/model/recording
sentinel hashes. The container was limited to two CPUs/2 GiB, had no host devices,
and was disconnected from the network during startup/removal. It was removed
afterward.

This proves RPM dependencies and startup, not KDE/Wayland, fonts, portal consent,
microphone capture or model inference on Fedora. Local evidence is
`build/validation/fedora-report.md` and `fedora-result.json`.

## ThoughtLoud rebrand candidate before workstation repairs: 2026-09-12

The source suite for this retained candidate passed **296 tests in 5.53 seconds** using
`QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv-build/bin/python -m pytest -q`.
Branding and source-collector review converged, including shell/desktop command
quoting, custom launcher preservation and a negative executable-hash check.
The release workflow's YAML and shell blocks were checked locally; the GitHub
workflow itself has not been run.

| Retained pre-repair artifact | SHA-256 |
| --- | --- |
| `thoughtloud_1.4.0-1_amd64.deb` | `679ed0642a9f80c3d49ed0828aa85260765999952549b79da667fea53334ad66` |
| `thoughtloud-1.4.0-1.x86_64.rpm` | `8ba000c940a004373f955f0725d19719db223e3b9604c403378cc131dcee98e0` |
| Bundled `thoughtloud` executable | `a84d985d9080111f2f37dff0159175e9a520f98e7244379ee3db2ad090736ff5` |
| `thoughtloud-1.4.0-dependency-sources.tar` | `0fb60b8fd8b9df8e3e08de014f97d1faa75d7dea7c4616bcc54569c99f8af1ca` |

Ubuntu's package manager replaced the old VOXD package with ThoughtLoud in the
retained Ubuntu 24.04 GNOME Wayland VM. Package-file verification passed; the
installed executable matched the final bundle. Both public commands and the old
absolute `/opt/voxd/voxd` executable work. The config and both downloaded GGUF
model files retained their hashes. Existing generated menu/login entries adopted
ThoughtLoud's name and SVG icon while preserving enabled state and the disabled
legacy service. Custom launcher behavior has separate focused tests.

After an actual VM reboot, the migrated XDG login entry started exactly one
`/opt/voxd/thoughtloud --tray` process. The typing daemon was active and the
legacy tray unit remained disabled. Opening settings reused that process;
the refreshed ThoughtLoud dock and tray icons rendered, with no Qt startup crash.
The configuration hash remained unchanged. The task VM was shut down after the
checks; its disk and local evidence are retained for reuse.

The unchanged GNOME shortcut, `/opt/voxd/voxd --trigger-record` on Ctrl+Alt+Space,
started and stopped a real virtual-microphone recording. The public 11-second
JFK fixture produced 106 printable ASCII characters through the managed CPU
runtime. Clipboard and archive transcript matched exactly; editor contents
matched with the intentional trailing insertion space and the editor's final
newline accounted for. The 24.9-second capture includes silence while the UI
harness waited before Stop. The selected default source remained at 37% volume
and unmuted. This is rebrand/upgrade integration evidence; the longer mixed-speech
and complete-tail evidence above remains the behavior baseline.

Fedora 44's isolated container replaced VOXD with the exact final ThoughtLoud RPM
offline, without a forced conflict-removal flag. Both wrappers and both absolute
executable paths worked. Nonroot settings and existing-instance IPC passed; the
frozen app loaded its bundled SVG icon plugin. A generated disabled, hidden
login entry received the new branding without changing those flags. Upgrade,
startup and removal preserved config/model/recording sentinel hashes. Both
commands and package integration files were removed on uninstall. The container
was removed after validation. This remains a package/startup smoke, not Fedora
desktop, portal, microphone or inference proof.

The separate dependency-source attachment is **1,512,673,280 bytes** and contains
241 pinned source archives, native server/helper sources and patches, and build
instructions. Verification binds the final app executable, 219 collected native
files and native helper files to their provenance. It covers 89 Ubuntu binary
packages from 67 source packages, the exact Qt source, and NumPy's embedded GCC
runtimes. All 5,869 Qt notice files match their manifest; installed license assets
and licensing docs match the frozen source files. All nested source archive
hashes were checked again from the completed attachment. The final source lock
is `f62e90d0724cd36c296508b9350703577b3fd1ab52b19480143c033dd6594db2`.

Matching application source is assembled as
`thoughtloud-1.4.0-source.tar.gz`, with an explicit uncommitted-candidate provenance
record and per-file hashes. Release checksums are in `dist/packages/SHA256SUMS`.
The CI recipe instead starts from the reviewed tag's commit and substitutes only
the generated source lock, recording that substitution in its source archive.
See [licensing.md](licensing.md) for distribution and rebuild instructions.

Local evidence: `build/validation/thoughtloud-*`,
`fedora-thoughtloud-result.json`, `fedora-thoughtloud-report.md`, and
`release-sources-report.json`. Physical microphone/headset checks and a supported
KDE portal walkthrough remain outside this candidate's proven support scope.

## Workstation startup and iGPU repair: 2026-09-12

The reboot report came from a source-backed workstation installation using its
existing Python/Qt runtime and external iGPU endpoint. It was not running the
frozen package tested in the VM. Three distinct faults were reproduced:

- The container used `/dev/dri/renderD129`, which disappeared when the Radeon
  780M became `renderD128`. Docker could not start it, so audio requests to port
  9394 failed with connection refused.
- The installed Qt 6.4.2 runtime lacked SVG rendering support. The SVG `QIcon`
  existed but yielded null pixmaps, leaving the tray item visually empty.
- This established configuration still had `setup_completed: false`. Its legacy
  tray service also attempted to start before the graphical display was ready.

The repaired host container uses the stable PCI render path for the same 780M,
with its original image, models, MTP settings, cache mounts, loopback port and
restart policy preserved. The stopped original container remains available for
rollback. The create-only installer now discovers the GPU by its vendor/device
identity, requires its matching PCI render symlink and passes a persistent
owned alias to Docker. This avoids both render-number dependence and the Docker
CLI's colon-delimited device syntax. Tests cover renumbering, another GPU taking
the old number, ambiguous selection and invalid PCI links. A disposable host
container confirmed that Docker preserves the alias and can start it twice.

Tray and window icons now use the original artwork rendered to bundled PNGs;
the SVG remains the canonical desktop artwork. All nine PNGs render in both
the old host Qt and the build Qt. A configured app waits up to five seconds for
a late tray before showing fallback settings. First-run and explicit settings
remain immediate. Beginning dictation cancels that delayed fallback, preventing
it from taking focus from the intended insertion destination.

After confirming the existing audio endpoint, the host configuration was marked
as set up. Login now uses the graphical session's XDG autostart entry; the legacy
tray unit is disabled and inactive. A user-local launcher consistently selects
the repaired source checkout. The existing Ctrl+. binding is unchanged and its
command points to that launcher. Other configuration, including the endpoint
and speech preference, was preserved. The typing daemon remains active.

Observed verification on the repaired host:

- The full source suite passed **313 tests in 7.25 seconds** with
  `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv-build/bin/python -m pytest -q`.
- Production file transcription of the public JFK clip succeeded before and
  after restarting the repaired container, with identical text. Runtime logs
  identify Radeon 780M at PCI `0000:c6:00.0` and active MTP: 20 draft tokens
  generated, 13 accepted in the post-restart request.
- The restarted tray registered with GNOME's StatusNotifier watcher and supplied
  nontransparent 22px and 64px icon pixmaps. No settings window was mapped after
  startup. Saving the autostart preference twice preserved the repaired launcher.
- Independent review and source/evidence traceability covered the device,
  tray-rendering, startup and delayed-focus regressions.

Local evidence is under `build/validation/host-repair-20260912/`, including
`pytest.txt`, `device-alias-proof.json`, `igpu-repair-result.json`,
`desktop-repair-result.json`, `tray-proof.json`, `audio-smoke.txt`,
`audio-after-restart.txt` and `llama-after-restart.log`. Backups in that directory
are private host repair material, not release attachments.

That repair verified the actual tray launch and inference restart. It did not
reboot the workstation, capture a physical microphone, inject text into a user
application or rebuild the packages. Managed-profile iGPU/MTP defaults remain
separate from restoring the host's established iGPU/MTP configuration; the
current managed candidate still omits the assistant model.

## Portable CPU candidate: 2026-09-12

The native recipe now enables the pinned library's dynamic backends and all CPU
variants. Fourteen CPU modules and the Vulkan module ship beside `llama-server`.
The library chooses a compatible CPU module at startup. Shared code and feature
detection are built without host-specific instruction flags; the Python AVX2
rejection was removed. Model downloads, MTP profile choices and runtime lifetime
were not changed by this work. The existing workstation iGPU service was kept
on its established image/configuration.

The complete desktop bundle still needs x86-64-v2. A QEMU `qemu64` guest with
only SSE2/SSE3 booted, but bundled Qt rejected the missing SSSE3, SSE4.1, SSE4.2
and POPCNT features. A Nehalem guest, with AVX, AVX2 and XSAVE absent, passed Qt
startup. This is a tested dependency boundary, not a claim that every historical
x86_64 CPU is supported.

The source suite passed **329 tests in 15.86 seconds** while native compilation
was running. Focused tests cover platform validation and rejection of incomplete
native backend output. Actual compiler commands confirm the server, shared base,
x64 fallback and feature-scoring object do not inherit AVX flags.

The final bundled native server was also exercised on the development host in
isolated containers, with public speech and the production transcription/VAD
code. CPU mode selected `libggml-cpu-zen4.so`; target and projector both used CPU.
Vulkan selected the Radeon 780M, with the established MTP flags and assistant:
20 draft tokens were generated and 13 accepted. Both produced the same expected
transcript. These are functional checks, not a hardware benchmark. All probe
containers were removed, and production identity, configuration, start time and
the tested native files remained unchanged.

The new DEB installed in the Nehalem VM, preserving configuration and existing
downloaded models. Package-file verification and app startup passed. The complete
virtual-microphone flow then passed through the installed Qt application, NumPy,
ONNX VAD, managed CPU transcription, clipboard and actual editor insertion.
The native runtime selected `libggml-cpu-sse42.so`; the guest exposed no AVX,
AVX2 or XSAVE. The 11.3-second capture completed in 144.2 seconds overall on this
four-vCPU guest. This proves compatibility, not comparable speed to accelerated
hardware. The saved editor matched the complete transcript plus the configured
trailing space and editor newline. The task VM was shut down after validation;
its isolated overlay and evidence are retained.

| Latest artifact | SHA-256 |
| --- | --- |
| `thoughtloud_1.4.0-1_amd64.deb` | `b8d0d01f94761132d84b5d1b7ea6e0eea54819cb0d849958aae74e2b6675c595` |
| `thoughtloud-1.4.0-1.x86_64.rpm` | `4d20aa9a751e6be16bfe60f41fea9e1d28d00b2568ca88ecf66b15fcbaa71b6a` |
| Bundled `thoughtloud` executable | `cca527f91ab7948b40f7d350ed7b8cc225c297d98613ef7fbc5b227be0c7b351` |
| `thoughtloud-1.4.0-dependency-sources.tar` | `90c7b7e30bf75e7676364382a66eb722b99dcaa229777fffe95dc068e1e6fd7a` |

The final source lock is
`1d96a074f35316cf2c9a69a8acb2c9676e7174672d93ee4ff380e595cc844a28`.
Verification covers 230 collected binaries and 241 hash-pinned source archives.
Wheel and Ubuntu source identities were unchanged, so their verified source
metadata/archives were reused while the actual binary inventory was regenerated.
Both packages contain all CPU modules, Vulkan and the repaired PNG icon states.
The new local dependency-source attachment is 1,512,683,520 bytes. Its embedded
lock, native build metadata, build recipe and licensing documentation match the
current files. Application source accompanies the candidate separately.

Evidence is under `build/validation/portable-cpu-20260912/`: `pytest.txt`,
`native-build.log`, `vm-cpu.txt`, `vm-upgrade.txt`, `host-probe-results.json`,
`vm-audio-result.json`, `vm-runtime.log`, `vm-typed.png`, `host-probe-summary.md`
and `package-summary.json`. Earlier package files remain
under `dist/pre-portable-packages-20260912/`. The RPM's backend/file inventory was
checked; its earlier Fedora desktop/startup evidence is not a new run of this
exact RPM. Physical microphones, broader hardware and supported portal consent
remain separate checks.

### Why a short dictation can still wait on cold loading

The reported cold request spent 20.3445 seconds starting the existing iGPU model
process before health readiness, then 0.3376 seconds on its actual one-token
warmup. Warmup was triggered during recording as intended. Model process startup
exceeded the approximately seven-second recording, leaving a delay after Stop.

An isolated timestamped probe kept the same production binary, model parameters
and a separate snapshot of its Mesa cache. Two starts took 12.03 and 6.41 seconds
inside the native process. Process-attributed physical reads fell from 7.93 GB
to 1.04 GB, and target mapping/allocation fell from 6.87 to 0.92 seconds. The
pinned loader eagerly populates model mappings (`MAP_POPULATE`/`WILLNEED`), so
model page-in/cache state is a measured contributor to startup variability.
The actual app warmup requests took 0.29 and 0.28 seconds.

The user's earlier run additionally spent 3.905 seconds reserving its large GPU
compute graph, compared with 0.625/0.221 seconds in the isolated runs. Its exact
memory/cache/contention state was not recorded, so the full historical 21 seconds
cannot be attributed precisely. Neither the five-minute idle policy nor loading
behavior was tuned during this diagnosis. Evidence and stage boundaries are in
`build/validation/cold-profile-20260912/findings.md` and `stage-summary.json`.

## Clean Linux desktop checks

Use clean QEMU/KVM snapshots with an ordinary logged-in desktop user:

| Environment | Required observations | Current status |
| --- | --- | --- |
| Ubuntu 24.04 GNOME Wayland, `.deb` | Dependency install, first-run settings, input access, shortcut route, editor insertion, reboot/autostart | Install/setup/download/shortcut/editor/upgrade/login passed; see walkthrough evidence below |
| Fedora KDE Wayland, `.rpm` | Same flow with KDE portal behavior and RPM dependencies | Pending |
| Desktop without a usable shortcut portal/tray | Visible settings and mute/error alerts, understandable manual shortcut fallback | GNOME 46 manual fallback, actual shortcut and visible mute alert passed |
| Real Linux hardware, CPU profile | Actual microphone capture, long dictation, memory/latency, restart and sleep/resume | Pending |
| Real Linux hardware, Vulkan profile | Audio correctness and device/driver compatibility, latency and memory | Public audio on Radeon 780M passed; external iGPU/MTP restart passed; physical microphone and broader hardware remain pending |

Start from no VOXD config, model cache, developer virtualenv, or preconfigured
user service. Test both new installation and an existing config without
`speech_preferences`. Feed repeatable PCM through a virtual microphone for
desktop integration checks; include at least one complete managed local model
download/start/dictation flow. A shared external inference endpoint can speed up
other desktop tests but cannot substitute for that local-install proof.

Check the following as part of the walkthrough:

- Default mic changes between recordings are followed; a muted mic blocks start,
  stopping bypasses preflight, and cancel/quit cannot allow a late start.
- Hotkey registration survives app restart, errors are visible, and supported
  desktops do not require an editor or terminal to complete setup.
- Interrupted download leaves no partial file treated as installed; retry is
  understandable. Runtime startup failure retains dictation audio where captured.
- Long recordings, quiet speech, silence and forced live-decode failure preserve
  the documented result/replay behavior. Clipboard failure does not block typing.
- The runtime remains alive through a long recording and replay; idle unload and
  quitting affect only the app's owned server, not an external endpoint.
- Package upgrade/removal, login startup and the desktop launcher behave as
  described without changing user microphone settings or deleting user history.

Use real hardware for GPU and physical microphone/performance claims. QEMU
software emulation may exercise behavior where KVM is unavailable; its timings
are not hardware-performance evidence.

## Before publication

Retain upstream attribution and use only the new ThoughtLoud artwork as product
branding. Verify the final source attachment against the shipped bundle and
include it with app source and dependency/model notices. Review the final package
contents, candidate evidence and remaining support exclusions. Publication,
release tagging and artifact upload are separate from local implementation and
must use the user's release authorization.
