"""A field that edits a name in place, over the text it replaces (docs/DESIGN.md
sections 8 and 9). Used for column, group and category names in the table header and
for table tab names.

- Enter, or a click elsewhere, keeps the text.
- Esc cancels.
- Tab and Shift+Tab keep the text and ask to move to the next or previous name.

The owner decides what "keep" means through ``on_commit(text, step)``. It returns True
when the name was taken (the field closes) or False when it was refused: the field
then stays open with its text selected, so it can be corrected. ``step`` is 1 for Tab,
-1 for Shift+Tab and 0 otherwise. When the field loses the focus with a refused name,
it closes and keeps the old name (``on_cancel`` is called), so a click elsewhere never
leaves a field behind.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QFocusEvent, QKeyEvent
from PySide6.QtWidgets import QLineEdit, QWidget

Commit = Callable[[str, int], bool]


class InlineEditor(QLineEdit):
    """A line edit shown over ``rect`` of ``parent`` with ``text`` selected. When it
    closes while it has the focus, the focus goes to ``restore_focus``."""

    def __init__(
        self,
        parent: QWidget,
        rect: QRect,
        text: str,
        on_commit: Commit,
        on_cancel: Callable[[], None] | None = None,
        restore_focus: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setObjectName("inlineEditor")
        self._on_commit = on_commit
        self._on_cancel = on_cancel
        self._restore_focus = restore_focus
        self._busy = False
        self._closed = False
        # As in the mockup: a thin Highlight border, so the field fits on the name's line.
        self.setFrame(False)
        self.setStyleSheet(
            "QLineEdit#inlineEditor { border: 1px solid palette(highlight);"
            " background: palette(base); padding: 0 3px; }"
        )
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setGeometry(rect)
        self.selectAll()
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    @property
    def is_closed(self) -> bool:
        return self._closed

    def commit(self, step: int = 0) -> bool:
        """Keep the text (as Enter, Tab or Shift+Tab would). True if the field closed;
        if the name was refused, the field stays open with the text selected."""
        taken = self._try(step)
        if not taken and not self._closed:
            self.selectAll()
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        return taken

    def _try(self, step: int) -> bool:
        if self._closed or self._busy:
            return self._closed
        self._busy = True  # focus moving to the next field must not commit again
        try:
            taken = self._on_commit(self.text(), step)
        finally:
            self._busy = False
        if taken:
            self._close()
        return taken

    def cancel(self) -> None:
        """Close without keeping the text."""
        if self._closed:
            return
        self._close()
        if self._on_cancel is not None:
            self._on_cancel()

    def _close(self) -> None:
        self._closed = True
        had_focus = self.hasFocus()
        self.hide()
        if had_focus and self._restore_focus is not None:
            self._restore_focus.setFocus(Qt.FocusReason.OtherFocusReason)
        self.deleteLater()

    def event(self, event: QEvent) -> bool:
        # Tab and Shift+Tab would move the focus before keyPressEvent sees them.
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if event.key() == Qt.Key.Key_Tab and not event.modifiers() & ~(
                Qt.KeyboardModifier.KeypadModifier
            ):
                self.commit(1)
                return True
            if event.key() == Qt.Key.Key_Backtab or (
                event.key() == Qt.Key.Key_Tab
                and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            ):
                self.commit(-1)
                return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt API)
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.commit(0)
            event.accept()
            return
        if key == Qt.Key.Key_Escape:
            self.cancel()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802 (Qt API)
        super().focusOutEvent(event)
        if self._closed or self._busy:
            return
        if event.reason() == Qt.FocusReason.PopupFocusReason:
            return  # e.g. the field's own context menu
        if not self._try(0):
            self.cancel()
