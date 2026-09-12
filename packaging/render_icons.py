#!/usr/bin/env python3
"""Render our canonical SVG artwork into PNGs for Qt without SVG plugins.

Run with the locked build environment: .venv-build/bin/python
packaging/render_icons.py [--check]. No installed desktop files are touched.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QBuffer, QIODevice, Qt
from PyQt6.QtGui import QGuiApplication, QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check renders without writing files")
    args = parser.parse_args()
    app = QGuiApplication([])
    assets = Path(__file__).resolve().parents[1] / "src/voxd/assets"
    paths = [assets / "thoughtloud.svg"] + [
        assets / f"thoughtloud-{state}-{frame}.svg"
        for state in ("recording", "working") for frame in range(1, 5)
    ]
    stale = []
    for path in paths:
        renderer = QSvgRenderer(str(path))
        if not renderer.isValid():
            raise SystemExit(f"Invalid SVG: {path}")
        image = QImage(256, 256, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        output = QBuffer()
        output.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(output, "PNG"):
            raise SystemExit(f"Could not render {path}")
        rendered = bytes(output.data())
        target = path.with_suffix(".png")
        if args.check:
            if not target.exists() or target.read_bytes() != rendered:
                stale.append(target.name)
        else:
            target.write_bytes(rendered)
    if stale:
        print("PNG renders need updating: " + ", ".join(stale))
        return 1
    print(f"{'Checked' if args.check else 'Rendered'} {len(paths)} ThoughtLoud icons.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
