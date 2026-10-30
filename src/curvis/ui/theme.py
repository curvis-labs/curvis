"""Light and dark themes, in the colours of the app icon (docs/DESIGN.md, section 3).

The Fusion style (with :class:`CurvisStyle`'s outlines) is used everywhere, so that both
themes look the same on every platform.
Widgets must only use palette roles, never fixed colours; every colour is set here.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle, QStyleOption, QWidget

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

# Outlines of fields, check boxes and radio buttons: Text mixed into Base, so they show
# in both themes with at least BORDER_CONTRAST_MIN (WCAG AA for parts of controls).
BORDER_MIX = 0.6
# Table grid lines: quieter than outlines, but visible on Base and AlternateBase.
GRID_MIX = 0.22
GRID_CONTRAST_MIN = 1.3
BORDER_CONTRAST_MIN = 3.0

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


def control_border(palette: QPalette) -> QColor:
    """The outline colour of fields, check boxes and radio buttons."""
    return _mix(palette, BORDER_MIX)


def grid_line(palette: QPalette) -> QColor:
    """The colour of the table's cell borders."""
    return _mix(palette, GRID_MIX)


def _mix(palette: QPalette, mix: float) -> QColor:
    """Text mixed into Base by ``mix`` (0 is Base, 1 is Text)."""
    base = palette.color(QPalette.ColorRole.Base)
    text = palette.color(QPalette.ColorRole.Text)
    return QColor.fromRgbF(
        base.redF() + (text.redF() - base.redF()) * mix,
        base.greenF() + (text.greenF() - base.greenF()) * mix,
        base.blueF() + (text.blueF() - base.blueF()) * mix,
    )


_E = QStyle.PrimitiveElement
_OUTLINED = {
    _E.PE_IndicatorCheckBox,
    _E.PE_IndicatorItemViewItemCheck,
    _E.PE_FrameLineEdit,
    _E.PE_Frame,
}


class CurvisStyle(QProxyStyle):
    """Fusion, with outlines, grid lines and close buttons that show in both themes.

    Fusion draws outlines a little darker than the window, which disappears on a dark
    window. Here fields, check boxes and radio buttons get :func:`control_border`, table
    grid lines get :func:`grid_line`, a checked toggle button (e.g. Calculate's "Not
    paired") gets the selection colour, and a tab's close button is a thin cross in the
    text colours instead of Fusion's bitmap.
    """

    def __init__(self) -> None:
        super().__init__("Fusion")

    def styleHint(  # noqa: N802 (Qt API)
        self,
        hint: QStyle.StyleHint,
        option: QStyleOption | None = None,
        widget: QWidget | None = None,
        return_data: object = None,
    ) -> int:
        if hint == QStyle.StyleHint.SH_Table_GridLineColor:
            palette = option.palette if option is not None else QApplication.palette()
            rgba = grid_line(palette).rgba()
            return rgba - (1 << 32) if rgba >= 1 << 31 else rgba  # a signed C int
        return super().styleHint(hint, option, widget, return_data)  # type: ignore[arg-type]

    def drawPrimitive(  # noqa: N802 (Qt API)
        self,
        element: QStyle.PrimitiveElement,
        option: QStyleOption,
        painter: QPainter,
        widget: QWidget | None = None,
    ) -> None:
        if element == _E.PE_IndicatorTabClose:
            self._draw_tab_close(option, painter)
            return
        super().drawPrimitive(element, option, painter, widget)
        if element in _OUTLINED or element == _E.PE_IndicatorRadioButton:
            enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
            if not enabled and element not in (_E.PE_FrameLineEdit, _E.PE_Frame):
                return
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(QPen(control_border(option.palette), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            rect = QRectF(option.rect).adjusted(0.5, 0.5, -0.5, -0.5)
            if element == _E.PE_IndicatorRadioButton:
                painter.drawEllipse(rect.adjusted(0.5, 0.5, -0.5, -0.5))
            else:
                painter.drawRoundedRect(rect, 2, 2)
            painter.restore()
        elif element == _E.PE_PanelButtonCommand and option.state & QStyle.StateFlag.State_On:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            highlight = option.palette.color(QPalette.ColorRole.Highlight)
            fill = QColor(highlight)
            fill.setAlphaF(0.25)
            painter.setPen(QPen(highlight, 1))
            painter.setBrush(fill)
            painter.drawRoundedRect(QRectF(option.rect).adjusted(0.5, 0.5, -1.5, -1.5), 2, 2)
            painter.restore()

    def _draw_tab_close(self, option: QStyleOption, painter: QPainter) -> None:
        """A thin cross: secondary text colour, text colour on a soft square when hovered."""
        state = option.state
        hovered = bool(state & (QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_Sunken))
        palette = option.palette
        rect = QRectF(option.rect)
        side = min(rect.width(), rect.height())
        box = QRectF(0, 0, side, side)
        box.moveCenter(rect.center())
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if hovered:
            fill = QColor(palette.color(QPalette.ColorRole.Text))
            fill.setAlphaF(0.25 if state & QStyle.StateFlag.State_Sunken else 0.14)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(box, 3, 3)
        role = QPalette.ColorRole.Text if hovered else QPalette.ColorRole.PlaceholderText
        pen = QPen(palette.color(role), 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        inset = side * 0.3
        cross = box.adjusted(inset, inset, -inset, -inset)
        painter.drawLine(cross.topLeft(), cross.bottomRight())
        painter.drawLine(cross.topRight(), cross.bottomLeft())
        painter.restore()


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
        app.setStyle(CurvisStyle())
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
