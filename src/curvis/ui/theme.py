"""Light and dark themes. The Fusion style is used everywhere so that both themes look
the same on every platform; widgets must only use palette roles, never fixed colours."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from curvis.settings import Theme

_DARK = {
    QPalette.ColorRole.Window: "#2b2b2e",
    QPalette.ColorRole.WindowText: "#e8e8ea",
    QPalette.ColorRole.Base: "#1f1f22",
    QPalette.ColorRole.AlternateBase: "#28282b",
    QPalette.ColorRole.ToolTipBase: "#3a3a3e",
    QPalette.ColorRole.ToolTipText: "#e8e8ea",
    QPalette.ColorRole.PlaceholderText: "#8c8c92",
    QPalette.ColorRole.Text: "#e8e8ea",
    QPalette.ColorRole.Button: "#36363a",
    QPalette.ColorRole.ButtonText: "#e8e8ea",
    QPalette.ColorRole.BrightText: "#ff6b6b",
    QPalette.ColorRole.Light: "#4a4a4f",
    QPalette.ColorRole.Midlight: "#3e3e43",
    QPalette.ColorRole.Mid: "#303034",
    QPalette.ColorRole.Dark: "#18181a",
    QPalette.ColorRole.Shadow: "#0c0c0d",
    QPalette.ColorRole.Highlight: "#3d7bd9",
    QPalette.ColorRole.HighlightedText: "#ffffff",
    QPalette.ColorRole.Link: "#6ea8ff",
    QPalette.ColorRole.LinkVisited: "#b493ff",
}
_DARK_DISABLED = {
    QPalette.ColorRole.WindowText: "#7a7a80",
    QPalette.ColorRole.Text: "#7a7a80",
    QPalette.ColorRole.ButtonText: "#7a7a80",
    QPalette.ColorRole.HighlightedText: "#b0b0b5",
    QPalette.ColorRole.Highlight: "#3a3a3e",
}


def dark_palette() -> QPalette:
    """The palette of the dark theme."""
    palette = QPalette()
    for role, color in _DARK.items():
        palette.setColor(role, QColor(color))
    for role, color in _DARK_DISABLED.items():
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(color))
    return palette


def light_palette(app: QApplication) -> QPalette:
    """The palette of the light theme (the Fusion style's own)."""
    return app.style().standardPalette()


def system_is_dark() -> bool:
    """True when the operating system asks for a dark colour scheme."""
    return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


def is_dark(palette: QPalette) -> bool:
    """True when ``palette`` has light text on a dark window."""
    return palette.color(QPalette.ColorRole.Window).lightness() < 128


class ThemeManager(QObject):
    """Applies the theme setting and, for "follow the system", tracks system changes."""

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._app = app
        self._theme = Theme.SYSTEM
        app.setStyle("Fusion")
        app.styleHints().colorSchemeChanged.connect(self._on_system_change)

    @property
    def theme(self) -> Theme:
        return self._theme

    def apply(self, theme: Theme) -> None:
        self._theme = Theme(theme)
        dark = system_is_dark() if self._theme is Theme.SYSTEM else self._theme is Theme.DARK
        self._app.setPalette(dark_palette() if dark else light_palette(self._app))

    def _on_system_change(self, _scheme: Qt.ColorScheme) -> None:
        if self._theme is Theme.SYSTEM:
            self.apply(Theme.SYSTEM)
