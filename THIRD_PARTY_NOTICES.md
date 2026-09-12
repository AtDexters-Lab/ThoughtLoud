# Third-party notices

## Linux desktop bundle

ThoughtLoud's application and portable core source retain their MIT license.
The combined desktop application is distributed under GPLv3 conditions because
it includes GPL-3.0-only PyQt6. Original MIT grants remain available. The bundle
also contains components with their own licenses:

- PyQt6 6.11.0 and Qt 6.11.2: license files under `_internal/licenses/`;
  PyQt6 uses GPLv3 in this build, and Qt contains LGPL/GPL components. Matching
  source, build and replacement instructions accompany the release. Qt's
  third-party notices include code used by its image and PDF plugins.
- PyInstaller 6.22.2: GPLv2-or-later with its bootloader exception; runtime
  hooks use Apache-2.0. Its license and matching sdist are supplied. The
  exception permits the unmodified bootloader to be combined with application
  code; other dependencies' licenses still apply.
- NumPy 2.5.3: BSD-3-Clause with OpenBLAS/LAPACK notices and GCC runtime license
  exceptions in the wheel's license file. ONNX Runtime 1.30.0: MIT and the
  third-party notices included in its wheel. The source lock records all
  remaining Python wheel versions; their license files accompany the bundle.
- Python and bundled Linux shared libraries: the Ubuntu builder's copyright
  notices for the actual collected files, plus referenced common license texts,
  are included under `_internal/licenses/system` and `common`.
- llama.cpp/Atomic source revision
  `0a635dcd92ba66c75fccfef91c3e106f4668f367`: native MIT license, exact source
  archive, local audio patch, build provenance and vendor notices accompany
  the server. Build recipe: `runtime/linux/build.sh`.
- ydotool 1.0.4, revision `57ba7d0af525e82da2de0e275d169477f293b197`:
  AGPL-3.0-or-later, with its license and corresponding unmodified source archive
  under `licenses/`. Build recipe: `packaging/build_ydotool.sh`.
- The separately downloaded Gemma 4 E4B Q8 model and F16 projector use Apache-2.0.
  Their pinned public revision, file sizes and SHA-256 digests are in
  `src/voxd/runtime/models.py`; the license is in `runtime/linux/LICENSE.gemma`.

`docs/licensing.md` describes release attachments, license terms, and build and
replacement instructions. `packaging/source-lock.json` ties dependency sources
to the actual collected binary hashes and package versions; the release source
collector verifies them before creating the separate dependency-source archive.
These notices do not change inherited artwork or name restrictions in
`ASSETS_LICENSE` and `TRADEMARKS.md`.

## Silero VAD

`src/voxd/assets/silero_vad_16k_op15.onnx` is distributed by the Silero VAD
project under the MIT License:

- Source: https://github.com/snakers4/silero-vad
- Model: `src/silero_vad/data/silero_vad_16k_op15.onnx`
- SHA-256: `7ed98ddbad84ccac4cd0aeb3099049280713df825c610a8ed34543318f1b2c49`

Copyright (c) 2019-present Silero Team

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
