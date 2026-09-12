# Linux package builds

The release candidate targets **amd64/x86_64 `.deb` and `.rpm` packages**. Build
on Ubuntu 24.04 with its distribution-provided Python 3.12. This keeps the
bundled Python library attributable to an exact Ubuntu source package. This is a candidate compatibility baseline;
clean GNOME/KDE installation and real hardware checks remain in
[the release checklist](../docs/linux-release.md). Other architectures and Arch
packages are not part of this release target.

The application is a PyInstaller **onedir** bundle installed under `/opt/voxd`.
It includes Python, Qt, ONNX Runtime, the Silero VAD asset, application dependencies,
and the built `llama-server` with its libraries. The launcher is `/usr/bin/thoughtloud`; `/usr/bin/voxd` remains an alias.
The `/opt/voxd` install path is retained for existing absolute shortcuts and units.
Pinned ydotool 1.0.4 helpers are included under `/opt/voxd/libexec`.
Model weights are downloaded by the user through setup; they are not embedded
in the OS package.

## Build the application and native runtime

Use a clean checkout and an x86_64 builder with C/C++ build tools, CMake, Vulkan
development headers, `glslc`, and Python 3.12 with venv support. The native build
uses a portable shared-code baseline and dynamically selected CPU variants,
without host-native tuning. Keep all backend modules in the native output. See
[`runtime/linux`](../runtime/linux/README.md) for its revision, patch and CPU
requirements.

Create the locked Python build environment from the repository root:

```bash
/usr/bin/python3.12 -m venv .venv-build
.venv-build/bin/python -m pip install --require-hashes -r packaging/requirements-build.txt
```

[`requirements-build.txt`](requirements-build.txt) pins the resolved application
and build dependencies with hashes. The input files are `pyproject.toml` and
`packaging/requirements-build.in`; dependency changes require regenerating and
reviewing this Python 3.12 lock. End-user installation does not run pip.

Build the native server, then include its output when bundling:

```bash
git clone https://github.com/AtomicBot-ai/atomic-llama-cpp-turboquant.git /tmp/voxd-llama-source
git -C /tmp/voxd-llama-source checkout --detach 0a635dcd92ba66c75fccfef91c3e106f4668f367
runtime/linux/build.sh /tmp/voxd-llama-source /tmp/voxd-llama-build
git clone https://github.com/ReimuNotMoe/ydotool.git /tmp/voxd-ydotool-source
git -C /tmp/voxd-ydotool-source checkout --detach 57ba7d0af525e82da2de0e275d169477f293b197
packaging/build_ydotool.sh /tmp/voxd-ydotool-source /tmp/voxd-ydotool-build
VOXD_LLAMA_RUNTIME=/tmp/voxd-llama-build/bin \
  VOXD_YDOTOOL_RUNTIME=/tmp/voxd-ydotool-build packaging/build_bundle.sh
```

Use new build-directory paths if those example paths already exist. The native
recipe applies the audio patch in an isolated worktree and records source,
patch and license information beside the binary. `build_bundle.sh` installs
the checkout into the build environment without resolving dependencies, builds
`dist/thoughtloud`, and checks the frozen executable's version with source-path
overrides removed. That smoke check proves executable startup, not microphone,
desktop or inference readiness.

The bundle script requires both native runtime and typing-helper outputs. The
application can still select an existing endpoint after installation. Its local
model profile is Gemma E4B
Q8 plus the F16 audio projector (about 9.2 GB), CPU or Vulkan, with MTP disabled.

## Produce native packages

Use nfpm **2.47.0** with a compatible Go toolchain:

```bash
go install github.com/goreleaser/nfpm/v2/cmd/nfpm@v2.47.0
export VERSION=1.4.0
export ARCH=amd64
nfpm pkg --packager deb -f packaging/nfpm.yaml --target dist/
nfpm pkg --packager rpm -f packaging/nfpm.yaml --target dist/
```

The bundle takes its version from `pyproject.toml`; `VERSION` controls nfpm's
package label. Update the project version before bundling when making a release,
and set `VERSION` to the same value. Use the same native
architecture throughout. The release workflow is
[`.github/workflows/release-packages.yml`](../.github/workflows/release-packages.yml);
artifact publication requires a separately approved release.

The release workflow also resolves the libraries actually collected by
PyInstaller, downloads their pinned source archives, and verifies the source
attachment before creating packages. Its application source snapshot contains
the reviewed Git commit plus that build's generated `source-lock.json`, with the
commit and manifest digest recorded in `RELEASE_PROVENANCE.json`. See the
[licensing guide](../docs/licensing.md) for local collection commands.

## Installed components and host dependencies

[`nfpm.yaml`](nfpm.yaml) is the authoritative package mapping. In addition to the
bundle and launcher, it installs `voxd-tray.desktop`, the icon, the user
`voxd-ydotoold.service`/tray units, and a `/dev/uinput` udev rule. The desktop basename is also
the shortcut portal's application identity; keep it in sync with `APP_ID` in
the Linux shortcut adapter.

The host supplies:

- a systemd user session and active-session `/dev/uinput` access for the bundled
  typing daemon;
- PipeWire/PulseAudio and its `pactl`/`parec` utilities, or a compatible PortAudio
  input path;
- `wl-clipboard`, PortAudio, and Qt's system XCB/EGL/OpenGL libraries;
- the declared glibc 2.39+, C++/OpenMP/OpenSSL libraries and Vulkan loader;
- a compatible Vulkan driver when using GPU inference;
- optional `ffmpeg` for FLAC history (WAV is retained if compression is unavailable).

Package hooks perform system integration; per-user setup belongs to the running
desktop session. Opening the installed application displays setup/settings.
First-run setup prepares the bundled user daemon; its status and retry control
remain available in settings. Packaged helpers use `~/.voxd_ydotool_socket` and
`voxd-ydotoold.service`; source installations retain the existing
`~/.ydotool_socket` and `ydotoold.service` defaults.
Configuration and downloaded models live in the user's XDG directories, outside
`/opt/voxd`.

For an upgrade from VOXD, quit the old application first and install the new
`thoughtloud` package normally. It replaces the `voxd` package while retaining
both command aliases, existing absolute shortcuts and the desktop application
identity. Standard generated menu/autostart files receive the new display name
on GUI startup; customized files are preserved.

Before uninstalling, turn off **Start at login** in settings and choose **Quit
app**, then remove the package with your package manager. Configuration, model
downloads and recording history are retained; remove those user-owned directories
separately only if you no longer need them.

The bundle collects dependency license/notice files and native source/patch
provenance, including ydotool source. See [docs/licensing.md](../docs/licensing.md) for the exact source attachment
and distribution terms. App-owned source and new SVG artwork retain MIT; the
combined PyQt desktop distribution follows GPLv3 and retains dependency terms.
Check the shipped notices and [release checklist](../docs/linux-release.md)
before publishing. ThoughtLoud is the product name; legacy `voxd` identifiers are retained only for
compatibility and upstream attribution.
