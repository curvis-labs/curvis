"""The app icon and the installer icons rendered from it."""

import struct
import sys
from pathlib import Path

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from curvis.ui.resources import APP_ICON, ICONS_DIR, app_icon

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packaging"))
import make_icons


def test_icon_files_ship_with_the_package() -> None:
    names = {p.name for p in ICONS_DIR.glob("*.svg")}
    assert names == {"curvis_dark.svg", "curvis_light.svg", "curvis_transparent.svg"}
    assert make_icons.APP_ICON == APP_ICON


def test_app_icon_renders(qapp: QApplication) -> None:
    icon = app_icon()
    assert not icon.isNull()
    assert icon.pixmap(32, 32).width() == 32


def test_render_png_has_requested_size_and_transparent_margin(qapp: QApplication) -> None:
    plain = QImage.fromData(make_icons.render_png(APP_ICON, 64))
    inset = QImage.fromData(make_icons.render_png(APP_ICON, 64, make_icons.MAC_INSET))
    assert (plain.width(), plain.height()) == (64, 64)
    assert plain.pixelColor(32, 32).alpha() == 255
    assert inset.pixelColor(2, 32).alpha() == 0  # the macOS margin is empty


def test_ico_and_icns_structure(qapp: QApplication, tmp_path: Path) -> None:
    files = make_icons.make_icons(tmp_path)

    ico = files["ico"].read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", ico)
    assert (reserved, kind, count) == (0, 1, len(make_icons.ICO_SIZES))
    for i, size in enumerate(sorted(make_icons.ICO_SIZES)):
        width, _, _, _, _, _, length, offset = struct.unpack_from("<BBBBHHII", ico, 6 + 16 * i)
        assert width == (0 if size == 256 else size)
        assert ico[offset : offset + 8] == b"\x89PNG\r\n\x1a\n"
        assert offset + length <= len(ico)

    icns = files["icns"].read_bytes()
    assert icns[:4] == b"icns"
    assert struct.unpack_from(">I", icns, 4)[0] == len(icns)
    pos, types = 8, []
    while pos < len(icns):
        types.append(icns[pos : pos + 4])
        assert icns[pos + 8 : pos + 16] == b"\x89PNG\r\n\x1a\n"
        pos += struct.unpack_from(">I", icns, pos + 4)[0]
    assert pos == len(icns)
    assert sorted(types) == sorted(make_icons.ICNS_TYPES.values())

    for size in make_icons.LINUX_SIZES:
        assert QImage(str(files[f"png{size}"])).width() == size
