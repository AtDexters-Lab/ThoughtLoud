# Linux release scope and evidence

The intended release is a Linux application for long, comfortable native and
code-switched dictation. A fresh user should install a package, configure a
shortcut and optional speech preference, download/use the model, and dictate
into an application without a developer environment or manual Python setup.

This page records release validation and support boundaries. Earlier evidence
below applies to its identified artifacts; retained candidates are not substitutes
for testing the final published package.

## Workstation reboot repair: 2026-09-13

The actual workstation reboot failed model autostart. The packaged tray and
single packaged typing daemon started successfully, but Docker could not start
the external iGPU container because its saved PCI path no longer existed.
The same Radeon 780M moved from PCI `c6:00.0` to `c8:00.0`; its render number also
changed. The earlier PCI-path repair covered render renumbering only and was
insufficient for this machine.

The external iGPU recipe now uses a udev alias based on hardware identity
(`1002:1900`). A one-shot startup service waits for Docker and that alias device
before validating and starting the container. Inside the container, the entrypoint
adds the render symlink corresponding to the actual device minor. This second
step is necessary: the live driver rejected physical minor 129 exposed as
`renderD128`, while the canonical `renderD129` symlink enumerated the 780M.
The single-780M support limit and startup/stop commands are documented in
[the iGPU guide](../runtime/igpu/README.md).

The repair was installed on this workstation. The original stopped container is
retained as `voxd-gemma-igpu-before-hardware-id-20260913` with automatic restart
disabled. The replacement preserves its image, model assets, inference flags,
MTP configuration and read-only mounts. Changes are limited to the GPU mapping
and entrypoint; Docker also normalizes the unset OOM-disable flag to false.
The published and installed application remains **1.4.1** with unchanged binary
SHA-256 `f5d74d169c43a785f574778c1daf9813e21a20689c975e7d4e3a8fb508e4de12`.
This is a repair to the external runtime recipe and deployment, not a rebuilt
application package; the 1.4.1 tag and release attachments are unchanged.

Validation:

- **376 tests passed**, including both PCI/render renumbering, missing/wrong
  aliases, ambiguous GPUs, stale Docker mappings, canonical render symlink
  handling, argument preservation and create-only rollback behavior.
- The installed udev alias identifies the current 780M; its systemd device is
  active and the startup service is enabled and completed successfully.
- The same failed 17-second recording completed in two segments, producing 222
  characters with no preference echo and positive MTP drafts in both responses.
  This cold replay took 18.16 seconds including model startup; it is not a new
  warm-latency benchmark. Recovery text remains private and was not typed into
  the desktop automatically.
- The rebooted tray owns one registered ThoughtLoud indicator. Exactly one
  packaged typing daemon is running; the keyboard monitor opens successfully and
  word pacing remains enabled. No keyboard events were emitted by this check.

Evidence is retained locally in `build/validation/reboot-20260913/`; private audio,
transcripts and Docker snapshots are not published. These checks prove the repair
works after activation in the current boot. **A further actual host reboot and
fresh physical dictation are still required to close reboot acceptance.**

## Preference echo fix: 1.4.1

Physical microphone testing after the 1.4.0 workstation deployment exposed a
regression: the model returned the configured speech preference for an empty
note and for the silent final chunk of a long note. The earlier release tests
did not cover this combination of non-empty preferences and silent audio.

Protocol 8 omits optional preferences, as well as prior transcript context, for
VAD-negative segments. It still transcribes their complete audio and retains
returned words. Speech-positive requests are unchanged. Both frozen prompts and
their selection rule are stored in the archived protocol and covered by its hash.

Validation of the patch:

- **345 tests passed.** Regression coverage includes first and trailing
  VAD-negative requests, custom base prompts, resumed speech context, archived
  protocol metadata, and retaining legitimately returned preference words.
- Both reported silent chunks return empty output on the existing local model,
  before and after speech controls. Replaying the full 92.85-second note preserves
  all nine spoken chunks exactly and removes the invented final chunk.
- Two public Hindi-English clips produce identical before/after transcripts.
  Quieter clips at gains 0.03 and 0.01 also retain intelligible Hindi-English
  through a deliberately forced VAD-negative path. At gain 0.001, real VAD is
  negative and both old and new prompts produce unreliable text: this patch
  does not establish accuracy for barely audible speech or eliminate every
  possible silence hallucination.
- The native inference runtime and existing dependency binaries are unchanged.
  This builder also collects `libxcb-cursor.so.0`, which was already a declared
  host package dependency. Its copyright notice and four matching source files
  are included. Source verification covers **231 binaries and 245 archives**.
- The exact 1.4.1 DEB passed in the Ubuntu 24.04 GNOME Wayland VM: the saved
  hotkey captured a silent virtual microphone, returned `no_speech` and inserted
  nothing. Public speech then passed through live VAD, the existing host Vulkan
  endpoint and editor insertion. Saved text matched exactly, accounting for the
  configured trailing space and the editor's final newline. Both archives record
  protocol 8 and the configured preference. The VM was shut down after the test.
- The exact RPM passed fresh Fedora 44 installation, clean `rpm -V`, all four
  command version checks, unprivileged offscreen Settings/IPC and removal. Its
  temporary container was removed. Both installed executables match SHA-256
  `f5d74d169c43a785f574778c1daf9813e21a20689c975e7d4e3a8fb508e4de12`.

Detailed private replay evidence and package checks are retained locally under
`build/validation/prompt-echo-20260912/`; user audio and transcripts are not
published. These tests support the reported echo fix. Workstation activation,
physical microphone retesting and an actual host reboot remain deployment steps.

### Published patch checkpoint

[ThoughtLoud 1.4.1](https://github.com/AtDexters-Lab/ThoughtLoud/releases/tag/v1.4.1)
is public and supersedes 1.4.0. Tag `v1.4.1` identifies reviewed commit
`aa1e1656346c1774f2666353c715242cd30346f2`. All five uploaded asset sizes and
GitHub SHA-256 digests match the local release artifacts. The application source
matches all 146 tagged files, with only release provenance added; both source
archives contain the same build lock.

An anonymous public DEB download and checksum list were also verified. DEB
SHA-256: `5712f4727cb10c420a10be172c35865f0a02cfeacf94b99dba5248b87dd6b868`.
The initial download stalled; a bounded retry completed successfully.

The workstation installed that public DEB on **2026-09-13**, after the desktop
was unlocked and native package authentication completed. The running 1.4.1
executable matches the release hash above. One tray registers with a Ready
tooltip and nontransparent 22/64-pixel icons; the typing service is active and
enabled. Configuration, the saved shortcut, microphone selection/mute/volume,
autostart, launcher and existing iGPU container were preserved.

The earlier activation preflight stopped before changing the installation or
tray because the locked GNOME session had temporarily removed its tray watcher.
The successful handoff waited for package installation to finish before launching
the replacement tray. Local evidence is in
`build/validation/prompt-echo-20260912/host-verification.json` and
`host-install-20260913.log`; guarded activation steps are in `install-host.py`.
Physical microphone testing then produced a 0.9-second `no_speech` note and two
spoken notes (4.6 and 7.55 seconds), all through live protocol 8, with no preference
echo. Actual workstation reboot verification remains open.

### Typing handoff audit: 2026-09-13

The user reported character-by-character appearance after publication. Comparing
pre-publication commit `03d4cce` with the released application confirms the same
word-pacing logic: zero configured character delay, 1 ms key hold and 10 ms pause
between word arguments. Both the previous and packaged helpers support that
path. Three paired runs into an isolated UNIX event receiver measured about
1.13 ms between character presses and 11.18 ms between words for both binaries.
This establishes client event pacing, not editor rendering latency. Individual
key events, rather than atomic word insertion, were used before publication too.

The audit did find an incomplete workstation handoff. The old typing daemon was
still running after its service file had been removed, alongside the packaged
daemon. Two identically named virtual keyboards prevented key-state monitoring,
so typing used compatibility cleanup after each bounded character chunk. That
did not disable word pacing, but it lost monitored recovery and reintroduced
cleanup pauses. Earlier workstation checks verified service activity and tray
registration without verifying that the key monitor could actually open.

The verified orphan was stopped, the packaged daemon restarted from installed
bytes, and the idle 1.4.1 tray relaunched on the packaged socket. A single legacy
socket export in the user's shell configuration was updated with a private
backup; the desktop login environment had no such override. One readable virtual
keyboard remains, and the actual typer's monitor opens and reaches a released
quiet state. Application/package bytes and word-pacing settings were unchanged.
Evidence is under `build/validation/typing-audit-20260913/`. Actual editor cadence
after the repair still requires user observation.

The same audit found recording-time warmup, live VAD-aligned decoding/full-WAV
recovery, previous-text context without assistant prefill, and the existing
external iGPU/MTP path retained. The requested microphone mute preflight is a new
step before capture/warmup and can add startup delay; it does not govern typing
cadence after transcription.

## Final release profile: 2026-09-12

The user approved publishing tagged packages with matching source attachments and
installing that exact release on the development workstation. The public repository
is now [AtDexters-Lab/ThoughtLoud](https://github.com/AtDexters-Lab/ThoughtLoud).

The explicit CPU/Vulkan choice remains. CPU is initially selected and uses only
the target and projector, with projector GPU offload disabled. Vulkan selects
Vulkan0 for target, projector and assistant and enables the validated MTP profile:
three draft tokens and adaptive skip disabled. No automatic GPU selection policy
or runtime change is applied to an existing external endpoint.

Vulkan adds a 100,259,232-byte assistant from AtomicChat revision
`69e1c34ad06437c136b935f6bf53ff80540c2361`, SHA-256
`eb576734fe210b551d091761fe83ab701c8e01ff708015a51172a4c0b04459e3`.
The current upstream main branch has a different export format. The pinned public
artifact is required. CPU-to-Vulkan setup reuses verified target/projector files;
profile controls are locked during a download, and the assistant follows the same
hash validation, cancellation and retry rules as the other models.

Final package validation before publication:

- Full application suite: **340 tests passed**; native recipe checks: **4 passed**
  after the build-description correction. Source verification covered **230 ELF
  files and 241 matching source archives**. All dependency binary/provider
  identities are unchanged from the portable candidate; application code and the
  descriptive native `BUILD.txt` were updated.
- Final DEB: `d54be99010bd12a8aa94cc472a30c91e3e7ca2cddaa06fb4b68a9b924003ec1f`.
  Installed in the Ubuntu 24.04 GNOME Wayland VM configured as Nehalem, with
  AVX, AVX2 and XSAVE absent. The saved Ctrl+Alt+Space desktop shortcut started
  and stopped capture of 11.4 seconds of public speech. VAD, managed SSE4.2 CPU
  inference and clipboard recovery passed; saved editor text exactly matched
  the transcript plus the normal insertion suffix. The 173.8-second total is a
  functional compatibility observation, not a CPU latency recommendation.
- The same DEB survived an actual VM reboot: one tray instance, visible icon,
  hidden settings window and working typing service. A post-reboot hotkey on a
  muted virtual default source showed the expected warning, retained mute and
  37% volume, and started neither capture nor inference. The VM was shut down.
- Final RPM: `eb072e1dd5755653897af77c19ffd47282fa49637091373ed94d1931c8b1adab`.
  Fresh Fedora 44 container installation resolved dependencies; `rpm -V`,
  unprivileged offscreen settings/IPC startup and removal passed. The container
  was removed. This does not establish Fedora desktop/audio behavior.
- Both installed executable hashes match the final bundle:
  `7d7317e6ca522335a81b542f7816230bf43a3a5918497101d397b40152d53693`.

Local detailed evidence is under `build/validation/release-20260912/`.
Managed Vulkan also passed through the actual `ManagedRuntime` lifecycle using
only the Radeon 780M: verified model acquisition, synthetic-audio readiness,
application warmup and two identical correct public-audio transcriptions. Each
speech request produced 20 MTP drafts and accepted 13; the second request
established repeated-request behavior. Target, projector and assistant used
Vulkan0. The native child exited successfully, the isolated container was removed,
and the production endpoint and model/runtime files were unchanged. Public
assistant acquisition independently downloaded and verified the exact pinned
100,259,232-byte file.

The tag and its application-source attachment bind this reviewed source tree.
The release publishes DEB, RPM, application source, complete dependency source
and `SHA256SUMS` together. Workstation installation and actual physical microphone
and host reboot verification are deployment steps after publication; the results
above do not imply those steps have happened.

### Published release and workstation deployment

[ThoughtLoud 1.4.0](https://github.com/AtDexters-Lab/ThoughtLoud/releases/tag/v1.4.0)
is public. The annotated tag identifies commit
`b78a9e8cccf0c07c79c1a94a530f6d56ce7688b7`. All five uploaded assets were checked
against their local sizes and GitHub SHA-256 digests. An anonymous download of
the public DEB and checksum list passed verification before workstation install.
The application-source archive matches all 146 tagged files, with only the
additional release-provenance file; both source archives contain the same lock.

The workstation now runs that released executable, with the package-owned typing
service active and enabled. Its tray registers once with a Ready tooltip and
nontransparent icon pixels. The saved Ctrl+. shortcut, all configuration values,
USB default microphone selection, mute state and 45% volume were preserved.
Autostart invokes the packaged executable. The existing iGPU endpoint remains
independently running and answers model discovery.

The first workstation handoff attempted activation before package unpacking had
finished and stopped at its installed-file check. Repeating activation after the
verified installation completed repaired the user-local source launcher and
replaced the idle source tray. This was a deployment sequencing error; it did
not require changes to the released application.

Physical microphone/editor confirmation and an actual workstation reboot remain
open. Tray registration is not a substitute for visual desktop verification,
and the VM reboot evidence above does not establish a workstation reboot.

The existing five-minute unload policy remains; this release does not claim to
eliminate cold model-loading latency. Physical microphone and actual workstation
reboot checks are separate from VM and prerecorded-audio evidence.

The records below retain earlier checkpoints and their original validation
boundaries. Their then-pending items are superseded by the final release evidence
above where explicitly completed.

## Repository checkpoint: 2026-09-12

Implementation checkpoint: `3e838d3137ef3f5f4b1a4fbfca3c9fe126e6eb35`
(`feat: checkpoint ThoughtLoud Linux release candidate`). Its complete 146-file
Git tree matches the validated application-source snapshot byte for byte.
This subsequent documentation update records closure without changing that
tested implementation or rewriting the immutable candidate artifacts.

The checkpoint includes ThoughtLoud branding, the platform-independent dictation
session and Linux adapters, setup/hotkey/microphone behavior, bundled runtime and
verified model downloads, startup/tray fixes, stable iGPU device selection,
portable CPU dispatch, licensing/source attachments and release build recipes.
Validation is complete for the documented candidate scope: 329 tests; installed
non-AVX DEB dictation through VAD and editor insertion; optimized CPU and Radeon
780M/MTP audio; and verification of 230 binaries and 241 source archives.
Implementation review and final traceability checks found no blocking omissions.
Task VMs and temporary inference containers were stopped after validation.

The application-source archive is
`thoughtloud-1.4.0-source.tar.gz`, SHA-256
`a59fd733fae23cef7439d120c222a52b251536e227f3fa5383e03c6ad1d8f51b`.
Its provenance correctly records assembly from the pre-commit working tree; the
commit above now identifies those exact source bytes. Package and dependency
source identities are in the portable CPU candidate section below. Build outputs,
models, recordings, VM disks/keys and private host-repair backups are local ignored
artifacts, not repository contents. The reviewed Qt notice corpus is committed.

Project-file whitespace checks passed. Two unchanged upstream license copies
(`PyInstaller-COPYING.txt` and `libquadmath-LGPL-2.1.txt`) retain their original
whitespace; their bytes were verified against the validated source snapshot.

Remaining follow-ups are explicit:

- Investigate cold-load optimization; the five-minute unload policy and current
  loading behavior remain in place.
- Decide and implement any managed iGPU/MTP default change. The managed profile
  currently omits the assistant; the established external iGPU/MTP profile works.
- Extend physical microphone/headset and KDE portal coverage before advertising
  those environments as fully validated.
- For a downloadable binary release, select a reviewed version/tag and execute
  the release workflow with matching package, application-source and dependency-
  source attachments. A repository push does not run that manual workflow.

These follow-ups do not block the requested source checkpoint and repository
push. This closes the current implementation workstream; it does not represent
completion of those follow-ups or a tagged binary release.

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

The original candidate was built from an uncommitted working tree based on
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
