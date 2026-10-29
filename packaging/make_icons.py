"""Render the app icon for each platform from the SVG in src/curvis/ui/icons.

    uv run python packaging/make_icons.py [output folder]   # default: build/icons

Writes ``curvis.ico`` (Windows), ``Curvis.icns`` (macOS) and PNGs for Linux. ICO and
ICNS files are written by hand with PNG-compressed entries, which every supported
Windows and macOS version reads, so no extra tools are needed. Called by curvis.spec.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ICONS = Path(__file__).resolve().parents[1] / "src" / "curvis" / "ui" / "icons"
APP_ICON = ICONS / "curvis_dark.svg"
"""The icon used for the app and for .cvp files on every platform."""

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 256)
# ICNS entry types that hold PNG data, by pixel size.
ICNS_TYPES = {
    16: b"icp4",
    32: b"icp5",
    64: b"icp6",
    128: b"ic07",
    256: b"ic08",
    512: b"ic09",
    1024: b"ic10",
}
LINUX_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
MAC_INSET = 100 / 1024
"""macOS icons leave a margin around the artwork (Apple's grid: 824 of 1024 px)."""

_app: object = None


def render_png(svg: Path, size: int, inset: float = 0.0) -> bytes:
    """``svg`` rendered as a ``size`` x ``size`` PNG, with ``inset`` (a fraction) empty
    on each side."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    global _app
    if QGuiApplication.instance() is None:
        # Kept for the life of the process: Qt cannot reliably create a second one.
        _app = QGuiApplication(["make_icons", "-platform", "offscreen"])
    renderer = QSvgRenderer(str(svg))
    if not renderer.isValid():
        raise ValueError(f"Cannot read {svg}")
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    margin = size * inset
    renderer.render(painter, QRectF(margin, margin, size - 2 * margin, size - 2 * margin))
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data.data())


def ico_bytes(pngs: dict[int, bytes]) -> bytes:
    """An ICO file holding one PNG per size."""
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = len(header) + 16 * len(pngs)
    entries, blobs = b"", b""
    for size, png in sorted(pngs.items()):
        dim = 0 if size >= 256 else size  # 0 means 256
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        blobs += png
    return header + entries + blobs


def icns_bytes(pngs: dict[int, bytes]) -> bytes:
    """An ICNS file holding one PNG per size."""
    body = b"".join(
        ICNS_TYPES[size] + struct.pack(">I", 8 + len(png)) + png
        for size, png in sorted(pngs.items())
    )
    return b"icns" + struct.pack(">I", 8 + len(body)) + body


def make_icons(out: Path, svg: Path = APP_ICON) -> dict[str, Path]:
    """Write all icon files to ``out``; returns them by kind."""
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "ico": out / "curvis.ico",
        "icns": out / "Curvis.icns",
        "svg": out / "org.curvis.Curvis.svg",
    }
    files["ico"].write_bytes(ico_bytes({s: render_png(svg, s) for s in ICO_SIZES}))
    files["icns"].write_bytes(icns_bytes({s: render_png(svg, s, MAC_INSET) for s in ICNS_TYPES}))
    files["svg"].write_bytes(svg.read_bytes())
    for size in LINUX_SIZES:
        path = out / "hicolor" / f"{size}x{size}" / "apps" / "org.curvis.Curvis.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(render_png(svg, size))
        files[f"png{size}"] = path
    return files


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("build/icons")
    for kind, path in make_icons(target).items():
        print(f"{kind:>6}  {path}")
