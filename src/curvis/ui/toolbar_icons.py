"""Toolbar icons: the two-tone SVG line icons in ``icons/toolbar/``, coloured from the
palette when they are drawn (docs/DESIGN.md, section 7).

Each file draws the object in navy (#1d3557) and the action in teal (#00a6a6). When an
icon is drawn those colours are replaced by palette colours, so one file serves both
themes and the icons follow a theme change at once:

| Icon state | Object (navy) | Action (teal) |
| --- | --- | --- |
| Normal | ``ButtonText`` | ``Link`` |
| Disabled | disabled ``ButtonText`` | disabled ``ButtonText`` |
| Highlighted menu row (Active, Selected) | ``HighlightedText`` | ``HighlightedText`` |

Toolbar buttons keep the Normal colours when hovered; :class:`~curvis.ui.theme.CurvisStyle`
draws them that way.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QIcon, QIconEngine, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from curvis.ui.resources import ICONS_DIR

TOOLBAR_ICONS_DIR = ICONS_DIR / "toolbar"
OBJECT_COLOR = "#1d3557"
ACTION_COLOR = "#00a6a6"
ICON_SIZE = 20

_R = QPalette.ColorRole
_G = QPalette.ColorGroup


def icon_colors(palette: QPalette, mode: QIcon.Mode) -> tuple[str, str]:
    """(object colour, action colour) for an icon in ``mode``."""
    if mode == QIcon.Mode.Disabled:
        disabled = palette.color(_G.Disabled, _R.ButtonText).name()
        return disabled, disabled
    if mode in (QIcon.Mode.Active, QIcon.Mode.Selected):
        highlighted = palette.color(_G.Active, _R.HighlightedText).name()
        return highlighted, highlighted
    return (
        palette.color(_G.Active, _R.ButtonText).name(),
        palette.color(_G.Active, _R.Link).name(),
    )


def recolored(svg: str, object_color: str, action_color: str) -> str:
    """``svg`` with the two tones replaced."""
    text = svg.replace(OBJECT_COLOR, "\0object\0").replace(OBJECT_COLOR.upper(), "\0object\0")
    text = text.replace(ACTION_COLOR, action_color).replace(ACTION_COLOR.upper(), action_color)
    return text.replace("\0object\0", object_color)


class PaletteIconEngine(QIconEngine):
    """Draws one two-tone SVG with the colours of the application palette."""

    def __init__(self, svg: str) -> None:
        super().__init__()
        self._svg = svg
        self._cache: dict[tuple[int, int, int, int, int], QPixmap] = {}

    def clone(self) -> QIconEngine:
        return PaletteIconEngine(self._svg)

    def _renderer(self, mode: QIcon.Mode) -> QSvgRenderer:
        palette = QApplication.palette()
        text = recolored(self._svg, *icon_colors(palette, mode))
        return QSvgRenderer(QByteArray(text.encode()))

    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State) -> None:
        self._renderer(mode).render(painter, rect)

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        key = (
            size.width(),
            size.height(),
            int(mode.value),
            int(state.value),
            QApplication.palette().cacheKey(),
        )
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paint(painter, QRect(QPoint(0, 0), size), mode, state)
        painter.end()
        if len(self._cache) > 64:
            self._cache.clear()
        self._cache[key] = pixmap
        return pixmap

    def availableSizes(  # noqa: N802 (Qt API)
        self, mode: QIcon.Mode = QIcon.Mode.Normal, state: QIcon.State = QIcon.State.Off
    ) -> list[QSize]:
        return [QSize(ICON_SIZE, ICON_SIZE)]


def toolbar_icon(name: str) -> QIcon:
    """The toolbar icon ``icons/toolbar/<name>.svg``."""
    path: Path = TOOLBAR_ICONS_DIR / f"{name}.svg"
    return QIcon(PaletteIconEngine(path.read_text(encoding="utf-8")))
