"""The launcher: shown at start-up and whenever no project is open (docs/DESIGN.md §10).

One centred column, 760 px wide (narrower windows: the full width with the 12 px margin):
a row of tiles to start from, the *Recent projects* heading with *Open project…* at its
right, and the list of recent projects. Every colour comes from the palette, so the
launcher follows the theme.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import (
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QFocusEvent,
    QFont,
    QFontMetrics,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPalette,
    QPen,
    QPixmap,
    QShowEvent,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from curvis.settings import RecentProject
from curvis.ui.dialogs import MARGIN, SECTION_SPACING, SPACING, EmptyState, set_tab_order
from curvis.ui.relative_time import format_relative
from curvis.ui.resources import ICONS_DIR
from curvis.ui.theme import control_border, grid_line
from curvis.ui.toolbar_icons import recolored, toolbar_icon

COLUMN_WIDTH = 760
"""Width of the launcher's column; narrower windows use the full width."""
TOP_MARGIN = 32
BOTTOM_MARGIN = 24
TILE_WIDTH = 178
PICTURE_HEIGHT = 104
TILE_SPACING = 12
"""Between tiles, across and down."""
TILE_TEXT_GAP = 6
"""Between a tile's picture and its name."""
ROW_HEIGHT = 48
ROW_PADDING = 12
FILE_ICON_SIZE = 28
HOVER_ALPHA = 0.07
"""The hovered row is shaded with ``Text`` at this opacity."""
EMPTY_TEXT = "Projects you open or save will appear here."
MISSING_TEXT = "File not found"

_R = QPalette.ColorRole
_G = QPalette.ColorGroup
PathRole = Qt.ItemDataRole.UserRole
MissingRole = Qt.ItemDataRole.UserRole + 1
FolderRole = Qt.ItemDataRole.UserRole + 2
WhenRole = Qt.ItemDataRole.UserRole + 3


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _semibold(font: QFont) -> QFont:
    bold = QFont(font)
    bold.setWeight(QFont.Weight.DemiBold)
    return bold


# --- tiles ---------------------------------------------------------------------------

PictureFn = Callable[[QPainter, QRectF, QPalette], None]
"""Draws a tile's picture into a rectangle with the colours of a palette."""


@dataclass(frozen=True)
class Tile:
    """Something to start from: a blank project, or a sample project (step 2.9)."""

    key: str
    name: str
    detail: str
    """The secondary line under the name ("Sample"); empty for *Blank project*. The
    launcher shows no key combinations."""
    picture: PictureFn


BLANK_KEY = "blank"


def draw_plus(painter: QPainter, rect: QRectF, palette: QPalette) -> None:
    """The picture of *Blank project*: a thin plus in ``Link``."""
    pen = QPen(palette.color(_R.Link), 2.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    c, arm = rect.center(), 11.0
    painter.drawLine(QPointF(c.x() - arm, c.y()), QPointF(c.x() + arm, c.y()))
    painter.drawLine(QPointF(c.x(), c.y() - arm), QPointF(c.x(), c.y() + arm))


def blank_tile() -> Tile:
    return Tile(BLANK_KEY, "Blank project", "", draw_plus)


class TileButton(QAbstractButton):
    """A tile: a framed picture on ``Base``, the name under it and a secondary line.

    Hover and focus outline the picture in ``Highlight``. Enter and Space click it; the
    arrow keys move to the previous and next tile.
    """

    def __init__(self, tile: Tile, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tile = tile
        self.setText(tile.name)
        self.setAccessibleName(tile.name)
        if tile.detail:
            self.setAccessibleDescription(tile.detail)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        name = QFontMetrics(_semibold(self.font())).height()
        detail = self.fontMetrics().height()
        return QSize(TILE_WIDTH, PICTURE_HEIGHT + TILE_TEXT_GAP + name + detail)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        return self.sizeHint()

    def picture_rect(self) -> QRect:
        return QRect(0, 0, self.width(), PICTURE_HEIGHT)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt API)
        palette = self.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QRectF(self.picture_rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(control_border(palette), 1.0))
        painter.setBrush(palette.color(_R.Base))
        painter.drawRoundedRect(frame, 6, 6)
        self.tile.picture(painter, frame, palette)
        if self.underMouse() or self.hasFocus() or self.isDown():
            painter.setPen(QPen(palette.color(_R.Highlight), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(frame, 6, 6)

        name_font = _semibold(self.font())
        name_height = QFontMetrics(name_font).height()
        top = PICTURE_HEIGHT + TILE_TEXT_GAP
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        painter.setFont(name_font)
        painter.setPen(palette.color(_R.WindowText))
        name = QFontMetrics(name_font).elidedText(
            self.tile.name, Qt.TextElideMode.ElideRight, self.width()
        )
        painter.drawText(QRect(0, top, self.width(), name_height), flags, name)
        painter.setFont(self.font())
        painter.setPen(palette.color(_R.PlaceholderText))
        detail_rect = QRect(0, top + name_height, self.width(), self.fontMetrics().height())
        painter.drawText(detail_rect, flags, self.tile.detail)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt API)
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers():
            self.click()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            row = self.parentWidget()
            if isinstance(row, TileRow):
                row.move_focus(self, -1 if key == Qt.Key.Key_Left else 1)
        else:
            super().keyPressEvent(event)


class FlowLayout(QLayout):
    """Lays its items out left to right and wraps those that do not fit to a new row."""

    def __init__(self, parent: QWidget | None = None, spacing: int = TILE_SPACING) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 (Qt API)
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 (Qt API)
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 (Qt API)
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802 (Qt API)
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt API)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt API)
        return self._arrange(QRect(0, 0, width, 0), move=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 (Qt API)
        super().setGeometry(rect)
        self._arrange(rect, move=True)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        """All items in one row."""
        width = sum(i.sizeHint().width() for i in self._items)
        width += self._spacing * max(0, len(self._items) - 1)
        height = max((i.sizeHint().height() for i in self._items), default=0)
        return QSize(width, height)

    def minimumSize(self) -> QSize:  # noqa: N802 (Qt API)
        """One item per row."""
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _arrange(self, rect: QRect, *, move: bool) -> int:
        x, y, row_height = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x > rect.x() and x + hint.width() > rect.right() + 1:
                x, y, row_height = rect.x(), y + row_height + self._spacing, 0
            if move:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            row_height = max(row_height, hint.height())
        return y + row_height - rect.y()


class TileRow(QWidget):
    """The row of tiles. Tiles that do not fit wrap to a second row."""

    chosen = Signal(str)
    """The key of the tile that was clicked."""

    def __init__(self, tiles: Sequence[Tile], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = FlowLayout(self)
        self.buttons: list[TileButton] = []
        for tile in tiles:
            button = TileButton(tile, self)
            button.clicked.connect(lambda _checked=False, key=tile.key: self.chosen.emit(key))
            layout.addWidget(button)
            self.buttons.append(button)
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def move_focus(self, button: TileButton, step: int) -> None:
        index = self.buttons.index(button) + step
        if 0 <= index < len(self.buttons):
            self.buttons[index].setFocus(Qt.FocusReason.TabFocusReason)


# --- recent list ---------------------------------------------------------------------


@functools.cache
def _file_icon_svg() -> str:
    return (ICONS_DIR / "launcher" / "project_file.svg").read_text(encoding="utf-8")


_file_icon_cache: dict[tuple[str, str, float], QPixmap] = {}


def file_icon(page: QColor, data: QColor, ratio: float) -> QPixmap:
    """The recent-project file icon, 28 × 28, with the page and the data in these colours."""
    key = (page.name(), data.name(), ratio)
    pixmap = _file_icon_cache.get(key)
    if pixmap is None:
        svg = recolored(_file_icon_svg(), page.name(), data.name())
        side = round(FILE_ICON_SIZE * ratio)
        pixmap = QPixmap(side, side)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        QSvgRenderer(QByteArray(svg.encode())).render(painter)
        painter.end()
        pixmap.setDevicePixelRatio(ratio)
        _file_icon_cache[key] = pixmap
    return pixmap


def display_folder(folder: Path) -> str:
    """The folder as shown under a project's name: the home folder written as ``~``."""
    text = str(folder)
    if os.name == "posix":
        home = str(Path.home())
        if home not in ("", "/") and (text == home or text.startswith(home + os.sep)):
            return "~" + text[len(home) :]
    return text


class RecentDelegate(QStyledItemDelegate):
    """Paints a recent project: file icon, name (semibold) with the folder under it,
    elided in the middle, and the relative time at the right."""

    def sizeHint(  # noqa: N802 (Qt API)
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        return QSize(option.rect.width(), ROW_HEIGHT)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        view = option.widget
        rect: QRect = option.rect
        state = option.state
        palette: QPalette = view.palette() if view is not None else option.palette
        missing = bool(index.data(MissingRole))
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        if index.row() > 0:
            painter.setPen(grid_line(palette))
            painter.drawLine(rect.topLeft(), rect.topRight())
        if state & QStyle.StateFlag.State_MouseOver:
            shade = QColor(palette.color(_R.Text))
            shade.setAlphaF(HOVER_ALPHA)
            painter.fillRect(rect, shade)
        if state & QStyle.StateFlag.State_HasFocus and view is not None and view.hasFocus():
            painter.setPen(QPen(palette.color(_R.Highlight), 2.0))
            painter.drawRect(QRectF(rect).adjusted(1, 1, -1, -1))

        disabled = palette.color(_G.Disabled, _R.Text)
        secondary = palette.color(_R.PlaceholderText)
        icon = file_icon(
            disabled if missing else secondary,
            disabled if missing else palette.color(_R.Link),
            painter.device().devicePixelRatioF(),
        )
        icon_top = rect.top() + (rect.height() - FILE_ICON_SIZE) // 2
        painter.drawPixmap(rect.left() + ROW_PADDING, icon_top, icon)

        font = option.font
        metrics = QFontMetrics(font)
        when = str(index.data(WhenRole) or "")
        when_width = metrics.horizontalAdvance(when)
        right = rect.right() - ROW_PADDING
        painter.setFont(font)
        painter.setPen(secondary)
        painter.drawText(
            QRect(right - when_width, rect.top(), when_width + 1, rect.height()),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            when,
        )

        left = rect.left() + ROW_PADDING + FILE_ICON_SIZE + ROW_PADDING
        width = max(0, right - when_width - ROW_PADDING - left)
        name_font = _semibold(font)
        name_metrics = QFontMetrics(name_font)
        line = max(name_metrics.height(), metrics.height())
        top = rect.top() + (rect.height() - 2 * line) // 2
        name = name_metrics.elidedText(
            str(index.data(Qt.ItemDataRole.DisplayRole)), Qt.TextElideMode.ElideRight, width
        )
        folder = metrics.elidedText(
            str(index.data(FolderRole)), Qt.TextElideMode.ElideMiddle, width
        )
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        painter.setFont(name_font)
        painter.setPen(disabled if missing else palette.color(_R.Text))
        painter.drawText(QRect(left, top, width, line), flags, name)
        painter.setFont(font)
        painter.setPen(disabled if missing else secondary)
        painter.drawText(QRect(left, top + line, width, line), flags, folder)
        painter.restore()


class RecentList(QListWidget):
    """The recent projects. A left click or Enter asks to open a row; right-click, the
    menu key or Shift+F10 asks for its menu."""

    open_requested = Signal(int)
    """The row to open."""
    menu_requested = Signal(int, QPoint)
    """The row whose menu to show, at this global position."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("recentProjects")
        self.setAccessibleName("Recent projects")
        self.setItemDelegate(RecentDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover)
        self._pressed_row = -1

    def content_height(self) -> int:
        frame = 2 * self.frameWidth()
        return self.count() * ROW_HEIGHT + frame

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        hint = super().sizeHint()
        if self.count():
            hint.setHeight(self.content_height())
        return hint

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        hint = super().minimumSizeHint()
        hint.setHeight(min(self.content_height(), 2 * ROW_HEIGHT) if self.count() else 120)
        return hint

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        index = self.indexAt(event.position().toPoint())
        left = event.button() == Qt.MouseButton.LeftButton
        self._pressed_row = index.row() if left and index.isValid() else -1
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        super().mouseReleaseEvent(event)
        index = self.indexAt(event.position().toPoint())
        pressed, self._pressed_row = self._pressed_row, -1
        left = event.button() == Qt.MouseButton.LeftButton
        if left and index.isValid() and index.row() == pressed:
            self.open_requested.emit(index.row())

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        # One click opens; the second click of a double-click must not open it again.
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt API)
        key, mods = event.key(), event.modifiers()
        row = self.currentRow()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not mods:
            if row >= 0:
                self.open_requested.emit(row)
            event.accept()
        elif key == Qt.Key.Key_F10 and mods == Qt.KeyboardModifier.ShiftModifier:
            self._menu_for_current()
            event.accept()
        else:
            super().keyPressEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802 (Qt API)
        if event.reason() == QContextMenuEvent.Reason.Keyboard:
            self._menu_for_current()
            return
        index = self.indexAt(event.pos())
        if index.isValid():
            self.setCurrentRow(index.row())
            self.menu_requested.emit(index.row(), event.globalPos())

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802 (Qt API)
        if self.currentRow() < 0 and self.count():
            self.setCurrentRow(0)
        super().focusInEvent(event)

    def _menu_for_current(self) -> None:
        row = self.currentRow()
        if row < 0:
            return
        rect = self.visualItemRect(self.item(row))
        point = QPoint(rect.left() + ROW_PADDING + FILE_ICON_SIZE, rect.center().y())
        self.menu_requested.emit(row, self.viewport().mapToGlobal(point))


# --- the launcher --------------------------------------------------------------------


class LauncherColumn(QWidget):
    """The launcher's column: 760 px wide, or less when the window is narrower."""

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        hint = super().sizeHint()
        return QSize(COLUMN_WIDTH, hint.height())


class _Content:
    """The launcher's widgets and layout."""

    def __init__(self, launcher: QWidget, tiles: Sequence[Tile]) -> None:
        self.column = LauncherColumn(launcher)
        self.column.setMaximumWidth(COLUMN_WIDTH)
        self.tile_row = TileRow(tiles, self.column)

        self.recent_list = RecentList(self.column)
        self.recent_label = QLabel("&Recent projects", self.column)
        self.recent_label.setFont(_semibold(self.recent_label.font()))
        self.recent_label.setBuddy(self.recent_list)
        self.open_button = QPushButton(toolbar_icon("open_project"), "&Open project…", self.column)
        self.open_button.setToolTip("Open a project file")
        heading = QHBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(SPACING)
        heading.addWidget(self.recent_label, 0, Qt.AlignmentFlag.AlignBottom)
        heading.addStretch(1)
        heading.addWidget(self.open_button)

        self.empty_state = EmptyState(self.recent_list, EMPTY_TEXT)

        column = QVBoxLayout(self.column)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.tile_row)
        column.addSpacing(SECTION_SPACING + SPACING)
        column.addLayout(heading)
        column.addSpacing(SPACING)
        column.addWidget(self.recent_list)
        column.addStretch(0)
        self.column_layout = column

        self.outer = QHBoxLayout(launcher)
        self.outer.setContentsMargins(MARGIN, TOP_MARGIN, MARGIN, BOTTOM_MARGIN)
        self.outer.addWidget(self.column, 0, Qt.AlignmentFlag.AlignHCenter)

        set_tab_order([*self.tile_row.buttons, self.open_button, self.recent_list])


class LauncherWidget(QWidget):
    """Tiles to start from, *Open project…* and the list of recent projects."""

    new_requested = Signal()
    open_requested = Signal()
    recent_chosen = Signal(Path)
    """A recent project to open (its file exists)."""
    remove_requested = Signal(Path)
    """Take this project off the recent list; the file is not touched."""
    tile_chosen = Signal(str)
    """A tile other than *Blank project* was chosen (the sample projects, step 2.9)."""

    def __init__(
        self,
        parent: QWidget | None = None,
        clock: Callable[[], datetime] = _utc_now,
        tiles: Sequence[Tile] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("launcher")
        self._clock = clock
        self._tiles = list(tiles) if tiles is not None else [blank_tile()]
        self._projects: list[RecentProject] = []
        self._content: _Content | None = None

    def _ensure_built(self) -> _Content:
        """The launcher's widgets, made on first use (first show, or a test that looks at
        them), like the main toolbar, so building the main window stays quick."""
        if self._content is None:
            self._content = _Content(self, self._tiles)
            c = self._content
            c.tile_row.chosen.connect(self._on_tile)
            c.open_button.clicked.connect(self.open_requested)
            c.recent_list.open_requested.connect(self._open_row)
            c.recent_list.menu_requested.connect(self._show_menu)
            self._fill_list()
            # Made during the first show: Qt has shown the children already.
            c.column.show()
            c.outer.activate()
        return self._content

    @property
    def column(self) -> QWidget:
        return self._ensure_built().column

    @property
    def tile_row(self) -> TileRow:
        return self._ensure_built().tile_row

    @property
    def recent_label(self) -> QLabel:
        return self._ensure_built().recent_label

    @property
    def open_button(self) -> QPushButton:
        return self._ensure_built().open_button

    @property
    def recent_list(self) -> RecentList:
        return self._ensure_built().recent_list

    @property
    def empty_state(self) -> EmptyState:
        return self._ensure_built().empty_state

    @property
    def tiles(self) -> list[TileButton]:
        return self.tile_row.buttons

    @property
    def blank_tile(self) -> TileButton:
        return self.tile_row.buttons[0]

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 (Qt API)
        self._ensure_built()
        super().showEvent(event)
        if self.tiles:
            self.tiles[0].setFocus(Qt.FocusReason.OtherFocusReason)  # Enter starts a project

    def set_recent_projects(self, projects: Sequence[RecentProject]) -> None:
        """Show ``projects`` (newest first). Files that no longer exist are dimmed."""
        self._projects = list(projects)
        if self._content is not None:
            self._fill_list()

    def _fill_list(self) -> None:
        assert self._content is not None
        content = self._content
        recent = content.recent_list
        now = self._clock()
        recent.clear()
        for project in self._projects:
            exists = project.path.is_file()
            when = format_relative(project.opened_at, now) if exists else MISSING_TEXT
            item = QListWidgetItem(project.name)
            item.setData(PathRole, project.path)
            item.setData(MissingRole, not exists)
            item.setData(FolderRole, display_folder(project.path.parent))
            item.setData(WhenRole, when[:1].upper() + when[1:])
            item.setToolTip(str(project.path) if exists else f"{project.path} ({MISSING_TEXT})")
            recent.addItem(item)
        has_any = bool(self._projects)
        if has_any:
            recent.setCurrentRow(0)  # where Tab into the list lands
        policy = QSizePolicy.Policy.Maximum if has_any else QSizePolicy.Policy.Expanding
        recent.setSizePolicy(QSizePolicy.Policy.Preferred, policy)
        # An empty list fills the column; a list with rows takes the height of its rows.
        layout = content.column_layout
        layout.setStretch(layout.indexOf(recent), 0 if has_any else 1)
        layout.setStretch(layout.count() - 1, 1 if has_any else 0)
        recent.updateGeometry()

    def project_at(self, row: int) -> RecentProject:
        return self._projects[row]

    def is_missing(self, row: int) -> bool:
        return bool(self.recent_list.item(row).data(MissingRole))

    def open_row(self, row: int) -> None:
        """Open the project in ``row``; a missing file does not open."""
        self._open_row(row)

    def _open_row(self, row: int) -> None:
        if 0 <= row < len(self._projects) and not self.is_missing(row):
            self.recent_chosen.emit(self._projects[row].path)

    def recent_menu(self, row: int) -> QMenu:
        """The context menu of ``row``: *Open* and *Remove from list*."""
        path = self._projects[row].path
        menu = QMenu(self)
        open_action = menu.addAction("Open")
        open_action.setEnabled(not self.is_missing(row))
        open_action.triggered.connect(lambda: self._open_row(row))
        remove = menu.addAction("Remove from list")
        remove.triggered.connect(lambda: self.remove_requested.emit(path))
        menu.setDefaultAction(open_action)
        if open_action.isEnabled():
            menu.setActiveAction(open_action)
        return menu

    def _show_menu(self, row: int, pos: QPoint) -> None:
        menu = self.recent_menu(row)
        self._exec_menu(menu, pos)
        menu.deleteLater()

    def _exec_menu(self, menu: QMenu, pos: QPoint) -> None:
        """Run ``menu`` at ``pos`` (tests replace this)."""
        menu.exec(pos)

    def _on_tile(self, key: str) -> None:
        if key == BLANK_KEY:
            self.new_requested.emit()
        else:
            self.tile_chosen.emit(key)
