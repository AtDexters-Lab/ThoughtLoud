# Licensing and dependency sources

ThoughtLoud's own application and portable core source remain MIT licensed.
The Linux desktop application combines that code with **PyQt6 under GPL-3.0-only**;
the combined desktop application is distributed under GPLv3 conditions. The MIT
grant on the original files remains available. The separately executed ydotool
helper is AGPL-3.0-or-later. A commercial PyQt license is not needed for this
GPL-compatible distribution. See the [PyQt licensing FAQ](https://riverbankcomputing.com/commercial/license-faq).

Qt is dynamically linked. Its LGPLv3/GPLv3 license texts, PyQt's GPLv3 license,
PyInstaller's bootloader exception, wheel notices and Ubuntu copyright files
are provided in the installed `_internal/licenses` directory. The notices cover
the collected libraries, including the dependencies embedded by upstream wheels.
The [Qt license obligations](https://www.qt.io/development/open-source-lgpl-obligations)
and the actual license texts describe recipients' rights. Nothing in ThoughtLoud
restricts modification, replacement of these libraries, or reverse engineering
needed to debug such modifications.

## What accompanies a binary release

Publish these downloads together, with the same release version and checksums:

1. The Linux binary packages.
2. `thoughtloud-VERSION-source.tar.gz`: the exact application source, packaging
   scripts, dependency lock, model manifest and documentation used by the build.
3. `thoughtloud-VERSION-dependency-sources.tar`: the source archives listed in
   `packaging/source-lock.json`, exact native server/helper source and patches,
   build recipes, and this document. This is an uncompressed tar of already
   compressed upstream archives; end users do not need it to install the app.

Provide equivalent download access to the sources alongside the binaries. Do
not replace the attachment with a promise to provide it later or a collection
of upstream homepages. Keep the matching source attachments available for each
binary release. Source archives are not model weights; the independently
downloaded Gemma target, audio projector and MTP assistant have their own
Apache-2.0 license and pinned manifest. The assistant uses AtomicChat's
`69e1c34ad06437c136b935f6bf53ff80540c2361` revision; its SHA-256 is
`eb576734fe210b551d091761fe83ab701c8e01ff708015a51172a4c0b04459e3`.
See [third-party attribution](../THIRD_PARTY_NOTICES.md). Later assistant exports
use a different runtime format and are not interchangeable with this pin.

## Producing and checking the attachment

Use the same `.venv-build` and Ubuntu 24.04 builder that produced the bundle:

```bash
.venv-build/bin/python packaging/collect_sources.py lock
.venv-build/bin/python packaging/collect_sources.py fetch
.venv-build/bin/python packaging/collect_sources.py package \
  --output dist/packages/thoughtloud-1.4.0-dependency-sources.tar
```

`--bundle`, `--analysis`, `--lock`, `--directory`, and `--output` accept explicit
paths. `lock` obtains version-specific metadata from PyPI, the official Qt
download site and Ubuntu Launchpad. Review and retain this lock in the release
source. `fetch` downloads only those pinned URLs and verifies SHA-256 digests;
`verify` repeats verification offline, including the application executable and
the installed license/notice files. The Ubuntu descriptors identify the exact
source version and hash every upstream and packaging archive, retaining Ubuntu
patches and `debian/rules`. Qt's checksum comes from its official release server;
Python source distributions use the hashes published in PyPI's version API.

When updating Qt, use `collect_sources.py qt-notices` with the downloaded,
verified Qt source to regenerate `packaging/licenses/Qt-*-notices.tar.gz` and
its manifest, review them, then rebuild the bundle and regenerate its lock.
The notice archive retains license, attribution and related upstream README
files from the Qt modules used by this bundle, including PDF's Chromium code.
ICU's separate version-specific license is also supplied. Verification rejects
an old notice corpus or a bundle containing different notices.

The collector compares each collected native file with the build input and
records its hash and provider. It rejects a changed build environment, missing
archive, unknown binary provider or mismatched source identity. Always run it
against the **final** bundle: installing an Ubuntu security update after a build
can change the Python/library provider version. An old bundle must not be paired
with the current host's different source version.

The source lock records dependency provenance, not a claim that upstream wheels
can be reproduced byte for byte. Wheel build timestamps, compiler environments
and packaging are controlled by their publishers. The source attachment and
instructions support inspecting, modifying and rebuilding the corresponding
software; validation of a new build remains necessary.

## Rebuild or replace a component

The installed package uses `/opt/voxd` as a compatibility path. To experiment
without changing the installed app, quit ThoughtLoud and copy that directory
to a user-writable directory. Run the copied `thoughtloud` executable directly.
Keep the `_internal` directory beside it. There is no signature check or
application restriction preventing a modified library from loading.

- **Application and PyQt:** use the application source's
  `packaging/requirements-build.txt` and `packaging/build_bundle.sh`. To replace
  PyQt, unpack its matching sdist, build it with `sip-install` or `pip wheel`
  against the chosen compatible Qt `qmake`, install that wheel in `.venv-build`,
  and rebuild the application bundle. The PyQt sdist includes its SIP interface
  definitions and build configuration. See [PyQt source installation](https://www.riverbankcomputing.com/static/Docs/PyQt6/installation.html).
- **Qt:** unpack `qt-everywhere-src-6.11.2.tar.xz`. The source includes the Qt
  modules and their CMake/configure scripts and third-party code. Install its
  documented build prerequisites, create a separate build directory beside the
  source, then configure a shared release build from that directory with
  `../qt-everywhere-src-6.11.2/configure -prefix "$PWD/qt-install" -opensource -confirm-license -release
  -shared -nomake examples -nomake tests`; build with `cmake --build .` and
  `cmake --install .`. Build the modules used by the bundle (Qt Base, SVG,
  Wayland, image formats and PDF) with compatible features. Rebuild PyQt and
  the bundle against that installation, or replace compatible shared libraries
  and plugins under `_internal/PyQt6/Qt6`. The full source is intentionally
  supplied because PDF and image plugins bring additional third-party code.
  See [Qt Linux source build instructions](https://doc.qt.io/qt-6/linux-building.html).
- **Ubuntu shared libraries and Python:** run `dpkg-source -x PACKAGE.dsc`
  beside its accompanying `orig` and `debian` archives. The extracted
  `debian/control` specifies build prerequisites and `debian/rules` the actual
  distribution build recipe. Build in an Ubuntu 24.04 environment with
  `dpkg-buildpackage -b -uc -us`; extract the rebuilt packages and replace the
  compatible files recorded in the source lock, or rebuild ThoughtLoud against
  those packages. Do not replace the host's Python or libraries just to test a
  private copy of the app.
- **Native transcription server:** the attachment contains the exact upstream
  git archive, local audio patch and `BUILD.txt`. Use `runtime/linux/build.sh`
  with the pinned revision and patch; place the resulting server and shared
  libraries, including every `libggml-cpu-*.so` backend, together under
  `_internal/llama` or rebuild the bundle using
  `VOXD_LLAMA_RUNTIME`. The script states the CPU baseline and Vulkan options.
  It accepts a clean git checkout at the recorded revision. To rebuild directly
  from the supplied archive without git history, extract it, apply
  `voxd-audio.patch` with `patch -p1` inside `llama-source`, and run the equivalent
  CMake commands below.
- **Typing helper:** the exact ydotool source and `packaging/build_ydotool.sh`
  are included. Rebuild and replace `libexec/ydotool` and `libexec/ydotoold` in
  the private copy, or pass `VOXD_YDOTOOL_RUNTIME` to the bundle builder. A
  running helper must be restarted explicitly to use a replacement executable.
- **PyInstaller:** its sdist contains the bootloader source and build scripts.
  To rebuild it, use `python ./waf all` in its `bootloader` directory before
  installing the rebuilt PyInstaller and rerunning the bundle build. Its
  unmodified bootloader exception permits application distribution under the
  application's applicable license; it does not cancel dependency licenses.
  See [PyInstaller's license](https://pyinstaller.org/en/stable/license.html).

For an archive-based server build, after applying the supplied patch:

```bash
cmake -S llama-source -B llama-build \
  -DBUILD_SHARED_LIBS=ON -DGGML_BACKEND_DL=ON -DGGML_CPU_ALL_VARIANTS=ON \
  -DGGML_NATIVE=OFF -DGGML_AVX=OFF -DGGML_AVX2=OFF -DGGML_FMA=OFF -DGGML_F16C=OFF \
  -DGGML_BMI2=OFF -DGGML_SSE42=OFF -DGGML_VULKAN=ON -DGGML_CCACHE=OFF \
  -DLLAMA_CURL=OFF -DLLAMA_BUILD_SERVER=ON -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_TOOLS=ON -DCMAKE_BUILD_TYPE=Release
cmake --build llama-build --target llama-server -j2
```

For the unmodified helper archive, `cmake -S ydotool-1.0.4 -B ydotool-build
-DCMAKE_BUILD_TYPE=Release` and `cmake --build ydotool-build --target ydotool
ydotoold -j2` perform the compilation used by the helper build script without
requiring git history.

Library replacement requires compatible ABI, architecture and Python extension
version. Keep Qt plugins and Qt libraries from the same rebuilt installation.
Replacing a library may change functionality; run the focused tests and desktop
installation checks in `docs/linux-release.md` on the resulting build.

## Scope of the inventory

`packaging/source-lock.json` is the authority for exact wheel versions, Ubuntu
binary/source-package versions and collected binary hashes. Permissive
dependencies preserve their license and third-party notices; an upstream Git
repository URL alone is not represented as a complete recursive source archive.
ONNX Runtime is redistributed under MIT with its wheel's third-party notices.
NumPy's wheel also includes OpenBLAS/LAPACK and GCC runtime components, whose
separate notices and exceptions appear in NumPy's license file. The supplied
AlmaLinux GCC 8.5.0 source RPM covers the embedded libgfortran and libquadmath.
`packaging/licenses/NumPy-runtime-provenance.txt` records the exact signed RPM
identities, matching GNU build IDs and executable-section hashes. This source
is distinct from the Ubuntu GCC source used by other collected libraries.
The source RPM includes the patches and RPM spec needed to rebuild the libraries
using `rpmbuild --rebuild` in AlmaLinux 8.10 with the spec's build prerequisites.

The original VOXD artwork/trademark terms remain in `ASSETS_LICENSE` and
`TRADEMARKS.md` for provenance. They do not grant rights to an upstream brand or
override the license on newly created ThoughtLoud artwork.
