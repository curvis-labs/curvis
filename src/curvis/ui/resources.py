"""Files shipped with the app, such as icons."""

from pathlib import Path

from PySide6.QtGui import QIcon

ICONS_DIR = Path(__file__).with_name("icons")
APP_ICON = ICONS_DIR / "curvis_dark.svg"
"""The app icon. packaging/make_icons.py renders the installer icons from the same file."""


def app_icon() -> QIcon:
    """The app icon for windows, the taskbar and the dock."""
    return QIcon(str(APP_ICON))
