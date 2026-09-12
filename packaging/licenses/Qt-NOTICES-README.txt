Qt 6.11.2 and third-party notices

ThoughtLoud's Linux bundle dynamically links Qt libraries and plugins.
The GPL/LGPL license texts are provided as plain files in this directory.
ICU-73.2.txt covers the ICU libraries included by the PyQt6-Qt6 wheel.

Qt-6.11.2-notices.tar.gz preserves license, copyright, attribution and related
upstream README files from the matching official Qt source archive. Extract
it using an archive manager or `tar -xzf Qt-6.11.2-notices.tar.gz` to read the
individual files. Original source-relative paths identify their components.
The selected modules are Qt Base, SVG, Wayland, Image Formats and WebEngine
(which contains Qt PDF and its Chromium-derived third-party dependencies).
The corpus can include optional code not active in this particular build.

Qt-notices-manifest.json records every included file and SHA-256 digest,
the notice archive digest and the matching official full-source digest.
The full Qt source archive is provided in the separate dependency-source
release attachment; this small notice archive does not replace that source.

See the bundled licensing.md for rebuild and library replacement instructions.
The source collector rejects a bundle whose installed notices differ from
those matched to the release's pinned Qt source.
