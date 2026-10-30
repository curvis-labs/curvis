"""Light and dark themes, in the colours of the app icon (docs/DESIGN.md, section 3).

The Fusion style is used everywhere so that both themes look the same on every platform.
Widgets must only use palette roles, never fixed colours; every colour is set here.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from curvis.settings import Theme

_R = QPalette.ColorRole

# The icon's colours: navy #1D3557, teal #00A6A6, emerald #20C997, slate #667085 and
# off-white #F4F6F8. Teal is darkened to #007A7A for text and selection on light
# backgrounds, where the icon's teal is too light to read (3.0 : 1 on white).
_LIGHT = {
    _R.Window: "#f4f6f8",
    _R.WindowText: "#1d3557",
    _R.Base: "#ffffff",
    _R.AlternateBase: "#e0f2f1",  # calculated columns: a light teal tint
    _R.ToolTipBase: "#1d3557",
    _R.ToolTipText: "#f4f6f8",
    _R.PlaceholderText: "#667085",  # secondary text
    _R.Text: "#1d3557",
    _R.Button: "#e9edf2",
    _R.ButtonText: "#1d3557",
    _R.BrightText: "#b3261e",  # errors
    _R.Light: "#ffffff",
    _R.Midlight: "#f0f3f6",
    _R.Mid: "#c3cad4",
    _R.Dark: "#8a94a3",
    _R.Shadow: "#4a5568",
    _R.Highlight: "#007a7a",
    _R.HighlightedText: "#ffffff",
    _R.Link: "#007a7a",
    _R.LinkVisited: "#005f5f",
    _R.Accent: "#20c997",
}
_LIGHT_DISABLED = {
    _R.WindowText: "#9aa3b0",
    _R.Text: "#9aa3b0",
    _R.ButtonText: "#9aa3b0",
    _R.Highlight: "#c3cad4",
    _R.HighlightedText: "#ffffff",
}
_DARK = {
    _R.Window: "#152840",
    _R.WindowText: "#f4f6f8",
    _R.Base: "#0f1f33",
    _R.AlternateBase: "#1c3a4a",  # calculated columns: a dark teal tint
    _R.ToolTipBase: "#1d3557",
    _R.ToolTipText: "#f4f6f8",
    _R.PlaceholderText: "#9aa8bc",  # secondary text
    _R.Text: "#f4f6f8",
    _R.Button: "#1d3557",
    _R.ButtonText: "#f4f6f8",
    _R.BrightText: "#ff8080",  # errors
    _R.Light: "#2e4a70",
    _R.Midlight: "#24405f",
    _R.Mid: "#1a2f4a",
    _R.Dark: "#0a1626",
    _R.Shadow: "#050b14",
    _R.Highlight: "#00a6a6",
    _R.HighlightedText: "#0f1f33",
    _R.Link: "#00a6a6",
    _R.LinkVisited: "#20c997",
    _R.Accent: "#20c997",
}
_DARK_DISABLED = {
    _R.WindowText: "#5e7491",
    _R.Text: "#5e7491",
    _R.ButtonText: "#5e7491",
    _R.Highlight: "#24405f",
    _R.HighlightedText: "#9aa8bc",
}

# Text and background pairs that must reach CONTRAST_MIN in both themes (WCAG AA).
CONTRAST_MIN = 4.5
TEXT_PAIRS = (
    (_R.WindowText, _R.Window),
    (_R.Text, _R.Base),
    (_R.Text, _R.AlternateBase),
    (_R.ButtonText, _R.Button),
    (_R.PlaceholderText, _R.Window),
    (_R.PlaceholderText, _R.Base),
    (_R.BrightText, _R.Window),
    (_R.BrightText, _R.Base),
    (_R.BrightText, _R.AlternateBase),
    (_R.Link, _R.Window),
    (_R.Link, _R.Base),
    (_R.HighlightedText, _R.Highlight),
    (_R.ToolTipText, _R.ToolTipBase),
)


def _palette(
    colors: dict[QPalette.ColorRole, str], disabled: dict[QPalette.ColorRole, str]
) -> QPalette:
    palette = QPalette()
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    for role, color in disabled.items():
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(color))
    return palette


def dark_palette() -> QPalette:
    """The palette of the dark theme."""
    return _palette(_DARK, _DARK_DISABLED)


def light_palette() -> QPalette:
    """The palette of the light theme."""
    return _palette(_LIGHT, _LIGHT_DISABLED)


def contrast(a: QColor, b: QColor) -> float:
    """The WCAG contrast ratio of two colours, from 1 to 21."""

    def luminance(color: QColor) -> float:
        def channel(v: float) -> float:
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

        r, g, b = (channel(c) for c in (color.redF(), color.greenF(), color.blueF()))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


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
        self._app.setPalette(dark_palette() if dark else light_palette())

    def _on_system_change(self, _scheme: Qt.ColorScheme) -> None:
        if self._theme is Theme.SYSTEM:
            self.apply(Theme.SYSTEM)
