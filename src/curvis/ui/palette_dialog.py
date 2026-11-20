"""The palette editor: the colours a graph gives its series, in turn.

A list manager (docs/DESIGN.md §1): the palette's name, its colours as a list with a
swatch each, and Add…, Change…, Remove, Move up and Move down under the list. The page
and ink (axes and text) stay those of the palette it starts from, so graphs keep their
contrast.

The dialog edits a copy; :attr:`PaletteDialog.edited` is the result when it is
accepted. The Format dialog uses it for one graph; the Settings dialog to make, change
and choose the palette new graphs start with.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QWidget,
)

from curvis.charts.colors import CUSTOM_ID, MAX_PALETTE_COLORS, ColorScheme, normalized
from curvis.ui.dialogs import (
    DialogSkeleton,
    EmptyState,
    buddy_label,
    form_layout,
    secondary_label,
    set_tab_order,
    size_simple,
)

NOTE = "New series take these colors in turn. The page and the axes keep their colors."
NEEDS_NAME = "Give the palette a name."
NEEDS_COLOR = "A palette needs at least one color."
FULL = f"A palette has at most {MAX_PALETTE_COLORS} colors."


def swatch(color: str, size: int = 16) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(color))
    return QIcon(pixmap)


class PaletteDialog(QDialog):
    """Edit ``palette``. ``ok_text`` is the verb on the OK button ("Use", "Save")."""

    def __init__(
        self,
        palette: ColorScheme,
        *,
        title: str = "Edit palette",
        ok_text: str = "Save",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._base = palette
        self.skeleton = DialogSkeleton(self, ok_text)
        body = self.skeleton.body
        form = form_layout()
        self.name_edit = QLineEdit(palette.name)
        self.name_edit.setMaxLength(60)
        form.addRow(buddy_label("&Name", self.name_edit), self.name_edit)
        body.addLayout(form)
        self.colors = QListWidget()
        self.colors.setAccessibleName("Colors")
        self.colors.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.empty = EmptyState(self.colors, "No colors yet. Click Add… to choose one.")
        body.addWidget(self.colors, 1)
        row = QHBoxLayout()
        self.add_button = QPushButton("&Add…")
        self.change_button = QPushButton("C&hange…")
        self.remove_button = QPushButton("&Remove")
        self.up_button = QPushButton("Move &up")
        self.down_button = QPushButton("Move do&wn")
        for button in (
            self.add_button,
            self.change_button,
            self.remove_button,
            self.up_button,
            self.down_button,
        ):
            row.addWidget(button)
        row.addStretch(1)
        body.addLayout(row)
        body.addWidget(secondary_label(NOTE))
        for color in palette.series:
            self._add_item(color)
        self.colors.setCurrentRow(0)
        self.add_button.clicked.connect(self.add_color)
        self.change_button.clicked.connect(self.change_color)
        self.remove_button.clicked.connect(self.remove_color)
        self.up_button.clicked.connect(lambda: self.move_color(-1))
        self.down_button.clicked.connect(lambda: self.move_color(1))
        self.colors.currentRowChanged.connect(lambda _r: self._update())
        self.colors.itemDoubleClicked.connect(lambda _item: self.change_color())
        self.name_edit.textChanged.connect(lambda _t: self._update())
        assert self.skeleton.ok_button is not None
        set_tab_order(
            [
                self.name_edit,
                self.colors,
                self.add_button,
                self.change_button,
                self.remove_button,
                self.up_button,
                self.down_button,
                self.skeleton.ok_button,
            ]
        )
        self._update()
        size_simple(self)
        self.colors.setFocus()

    # --- result -----------------------------------------------------------------------

    @property
    def edited(self) -> ColorScheme:
        """The palette as edited (lower-case colours; a built-in one becomes custom)."""
        name = self.name_edit.text().strip()
        changed = self.series() != self._base.series or name != self._base.name
        palette_id = (
            self._base.id if not changed or self._base.id.startswith("user:") else CUSTOM_ID
        )
        return normalized(replace(self._base, id=palette_id, name=name, series=self.series()))

    def series(self) -> tuple[str, ...]:
        return tuple(
            str(self.colors.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self.colors.count())
        )

    # --- editing ----------------------------------------------------------------------

    def _add_item(self, color: str, row: int | None = None) -> None:
        item = QListWidgetItem(swatch(color), color.lower())
        item.setData(Qt.ItemDataRole.UserRole, color.lower())
        if row is None:
            self.colors.addItem(item)
        else:
            self.colors.insertItem(row, item)

    def _ask(self, start: str) -> str | None:
        chosen = QColorDialog.getColor(QColor(start), self, "Choose a color")
        return chosen.name().lower() if chosen.isValid() else None

    def add_color(self, color: str | None = None) -> None:
        """Add ``color`` (or one the user picks) after the selected colour."""
        if self.colors.count() >= MAX_PALETTE_COLORS:
            return
        current = self.colors.currentRow()
        start = self.series()[current] if current >= 0 else "#808080"
        color = color or self._ask(start)
        if color is None:
            return
        row = current + 1 if current >= 0 else self.colors.count()
        self._add_item(color, row)
        self.colors.setCurrentRow(row)
        self._update()

    def change_color(self, color: str | None = None) -> None:
        row = self.colors.currentRow()
        if row < 0:
            return
        color = color or self._ask(self.series()[row])
        if color is None:
            return
        item = self.colors.item(row)
        item.setIcon(swatch(color))
        item.setText(color.lower())
        item.setData(Qt.ItemDataRole.UserRole, color.lower())
        self._update()

    def remove_color(self) -> None:
        row = self.colors.currentRow()
        if row < 0:
            return
        self.colors.takeItem(row)
        self.colors.setCurrentRow(min(row, self.colors.count() - 1))
        self._update()

    def move_color(self, by: int) -> None:
        row = self.colors.currentRow()
        target = row + by
        if row < 0 or not 0 <= target < self.colors.count():
            return
        item = self.colors.takeItem(row)
        self.colors.insertItem(target, item)
        self.colors.setCurrentRow(target)
        self._update()

    def _update(self) -> None:
        row, count = self.colors.currentRow(), self.colors.count()
        self.add_button.setEnabled(count < MAX_PALETTE_COLORS)
        self.add_button.setToolTip("" if count < MAX_PALETTE_COLORS else FULL)
        for button in (self.change_button, self.remove_button):
            button.setEnabled(row >= 0)
        self.up_button.setEnabled(row > 0)
        self.down_button.setEnabled(0 <= row < count - 1)
        problem = None
        if not self.name_edit.text().strip():
            problem = NEEDS_NAME
        elif count == 0:
            problem = NEEDS_COLOR
        self.skeleton.set_problem(problem)


__all__ = ("PaletteDialog", "swatch")
