"""Shared building blocks for dialogs, as defined in docs/DESIGN.md.

- Spacing and size constants.
- :class:`DialogSkeleton`: the dialog's margins, its feedback line and its button row
  (secondary settings on the left, the platform's OK/Cancel order on the right), and
  OK enabled or disabled together with the reason why.
- :class:`FeedbackLine`: one line for an error, a warning or a status sentence.
- :class:`CollapsibleSection`: the collapsed "Advanced" section.
- :class:`FittedStack`: a stacked widget as tall as the page shown, not its tallest page.
- :class:`EmptyState`: a sentence shown over an empty list or table.
- Labels: :func:`secondary_label`, :func:`buddy_label`.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from itertools import pairwise

import shiboken6
from PySide6.QtCore import QAbstractItemModel, QEvent, QObject, QSize, Qt, Signal
from PySide6.QtGui import QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

MARGIN = 12
"""Dialog content margin, all sides."""
SPACING = 8
"""Between related controls; form rows; inside a group box."""
SECTION_SPACING = 16
"""Between the two columns, and between sections stacked vertically."""
INDENT = 20
"""Fields that belong to the radio button or check box above them."""
FIELD_WIDTH = 120
"""Short number and text fields."""

WORKBENCH_SIZE = QSize(900, 640)
WORKBENCH_MINIMUM = QSize(820, 540)
SIMPLE_MINIMUM_WIDTH = 360


class Severity(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


_ICONS = {
    Severity.WARNING: QStyle.StandardPixmap.SP_MessageBoxWarning,
    Severity.ERROR: QStyle.StandardPixmap.SP_MessageBoxCritical,
}


class FeedbackLine(QWidget):
    """An error (icon, error colour), a warning (icon) or a status sentence.

    It keeps the height of one line when empty, so the dialog does not jump.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("feedback")
        self.severity = Severity.INFO
        self.icon = QLabel(self)
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.label, 1)
        self.setMinimumHeight(self.fontMetrics().height())
        self.show_message("")

    def text(self) -> str:
        return self.label.text()

    def show_message(self, text: str, severity: Severity = Severity.INFO) -> None:
        self.severity = severity if text else Severity.INFO
        self.label.setText(text)
        self.label.setForegroundRole(
            QPalette.ColorRole.BrightText
            if self.severity is Severity.ERROR
            else QPalette.ColorRole.WindowText
        )
        pixmap = _ICONS.get(self.severity)
        if pixmap is None:
            self.icon.clear()
            self.icon.hide()
        else:
            size = self.fontMetrics().height()
            self.icon.setPixmap(self.style().standardIcon(pixmap).pixmap(size, size))
            self.icon.show()
        self.label.setAccessibleName(f"{self.severity.value}: {text}" if text else "")

    def clear(self) -> None:
        self.show_message("")


class DialogSkeleton:
    """Lays out ``dialog`` as DESIGN.md says: body, feedback line, button row.

    Put the dialog's own content in :attr:`body`, and secondary settings (naming,
    placement) in the button row with :meth:`add_secondary`. ``ok_text`` is the verb on
    the OK button; with ``close_only`` the dialog has a single Close button instead.
    """

    def __init__(
        self, dialog: QDialog, ok_text: str | None = None, *, close_only: bool = False
    ) -> None:
        self.dialog = dialog
        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        outer.setSpacing(SPACING)
        self.body = QVBoxLayout()
        self.body.setSpacing(SPACING)
        outer.addLayout(self.body, 1)
        self.feedback = FeedbackLine(dialog)
        outer.addWidget(self.feedback)
        self.button_row = QHBoxLayout()
        self.button_row.setSpacing(SPACING)
        self._secondary = QHBoxLayout()
        self._secondary.setSpacing(SPACING)
        self.button_row.addLayout(self._secondary)
        self.button_row.addStretch(1)
        standard = (
            QDialogButtonBox.StandardButton.Close
            if close_only
            else QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons = QDialogButtonBox(standard, dialog)
        self.ok_button: QPushButton | None = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if self.ok_button is not None and ok_text:
            self.ok_button.setText(ok_text)
        self.buttons.accepted.connect(dialog.accept)
        self.buttons.rejected.connect(dialog.reject)
        self.button_row.addWidget(self.buttons)
        outer.addLayout(self.button_row)
        self._elsewhere: QWidget | None = None

    def add_secondary(self, item: QWidget | QLayout, spacing: int = 0) -> None:
        """Add a secondary setting at the left of the button row."""
        if spacing:
            self._secondary.addSpacing(spacing)
        if isinstance(item, QLayout):
            self._secondary.addLayout(item)
        else:
            self._secondary.addWidget(item)

    def set_problem(
        self,
        problem: str | None,
        severity: Severity = Severity.INFO,
        *,
        shown_in: QWidget | None = None,
    ) -> None:
        """Disable OK while there is a ``problem`` and say why.

        The reason goes in the feedback line, unless ``shown_in`` (e.g. the error label
        under the field that causes it) already shows it.
        """
        if self.ok_button is not None:
            self.ok_button.setEnabled(problem is None)
        self._elsewhere = shown_in if problem is not None else None
        if problem is None or shown_in is not None:
            self.feedback.clear()
        else:
            self.feedback.show_message(problem, severity)

    def set_status(self, text: str, severity: Severity = Severity.INFO) -> None:
        """Show a status sentence or a warning; OK is not changed."""
        self.feedback.show_message(text, severity)

    def reason_shown(self) -> bool:
        """True when OK is enabled, or the reason it is disabled can be seen."""
        if self.ok_button is None or self.ok_button.isEnabled():
            return True
        if self.feedback.text():
            return True
        label = self._elsewhere
        return isinstance(label, QLabel) and bool(label.text()) and label.isVisible()


def size_workbench(dialog: QDialog) -> None:
    """Default and minimum size of a workbench dialog."""
    dialog.setMinimumSize(WORKBENCH_MINIMUM)
    dialog.resize(WORKBENCH_SIZE)


def size_simple(dialog: QDialog) -> None:
    """A simple dialog opens at its size hint, at least SIMPLE_MINIMUM_WIDTH wide."""
    hint = dialog.sizeHint()
    dialog.setMinimumWidth(max(SIMPLE_MINIMUM_WIDTH, dialog.minimumSizeHint().width()))
    dialog.resize(max(SIMPLE_MINIMUM_WIDTH, hint.width()), hint.height())


def columns(body: QBoxLayout, left: int = 1, right: int = 1) -> tuple[QVBoxLayout, QVBoxLayout]:
    """Add the two columns of a workbench dialog to ``body``; returns (left, right)."""
    row = QHBoxLayout()
    row.setSpacing(SECTION_SPACING)
    body.addLayout(row, 1)
    left_column, right_column = QVBoxLayout(), QVBoxLayout()
    for column in (left_column, right_column):
        column.setSpacing(SECTION_SPACING)
    row.addLayout(left_column, left)
    row.addLayout(right_column, right)
    return left_column, right_column


def choice_tabs(dialog: QDialog, name: str) -> QTabBar:
    """The tab bar for the choices across the top; Ctrl+Tab and Ctrl+Shift+Tab move it."""
    tabs = QTabBar(dialog)
    tabs.setExpanding(False)
    tabs.setDrawBase(True)
    tabs.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    tabs.setAccessibleName(name)

    def step(by: int) -> None:
        if tabs.count():
            tabs.setCurrentIndex((tabs.currentIndex() + by) % tabs.count())

    for keys, by in (("Ctrl+Tab", 1), ("Ctrl+Shift+Tab", -1)):
        shortcut = QShortcut(QKeySequence(keys), dialog)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(lambda by=by: step(by))
    return tabs


def form_layout() -> QFormLayout:
    """A form with DESIGN.md spacing and labels aligned left on every platform."""
    form = QFormLayout()
    form.setContentsMargins(0, 0, 0, 0)
    form.setHorizontalSpacing(SPACING + 4)
    form.setVerticalSpacing(SPACING)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return form


def buddy_label(text: str, buddy: QWidget, parent: QWidget | None = None) -> QLabel:
    """A label for ``buddy``; its mnemonic moves the focus there."""
    label = QLabel(text, parent)
    label.setBuddy(buddy)
    return label


def secondary_label(text: str = "", parent: QWidget | None = None) -> QLabel:
    """Secondary text (descriptions, hints) in the PlaceholderText colour, word-wrapped."""
    label = QLabel(text, parent)
    label.setWordWrap(True)
    label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
    return label


def error_label(parent: QWidget | None = None) -> QLabel:
    """A label for the error of one field, in the error colour, under that field."""
    label = QLabel(parent)
    label.setWordWrap(True)
    label.setForegroundRole(QPalette.ColorRole.BrightText)
    return label


class CollapsibleSection(QWidget):
    """A section that is closed until its title is clicked (DESIGN.md: "Advanced")."""

    toggled = Signal(bool)

    def __init__(self, title: str = "&Advanced", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.toggle = QToolButton(self)
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle.setAutoRaise(True)
        self.content = QWidget(self)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(INDENT, 0, 0, 0)
        self.content_layout.setSpacing(SPACING)
        self.content.setVisible(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING // 2)
        layout.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.content)
        self.toggle.toggled.connect(self._on_toggled)

    @property
    def expanded(self) -> bool:
        return self.toggle.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        self.toggle.setChecked(expanded)

    def _on_toggled(self, shown: bool) -> None:
        self.content.setVisible(shown)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow)
        self.toggled.emit(shown)


class FittedStack(QStackedWidget):
    """A stacked widget that takes the size of the page shown, not of its tallest page.

    Hidden pages get the Ignored size policy, which the stacked layout leaves out when it
    works out its size; the page shown gets its own policy back.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._policies: dict[int, QSizePolicy] = {}
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.currentChanged.connect(lambda _index: self.fit())

    def addWidget(self, widget: QWidget) -> int:  # noqa: N802 (Qt API)
        self._policies[id(widget)] = widget.sizePolicy()
        index = super().addWidget(widget)
        self.fit()
        return index

    def fit(self) -> None:
        ignored = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        for i in range(self.count()):
            page = self.widget(i)
            if page is None:
                continue
            own = self._policies.get(id(page), page.sizePolicy())
            page.setSizePolicy(own if i == self.currentIndex() else ignored)
        self.updateGeometry()


class EmptyState(QObject):
    """Shows ``text`` centred over ``view`` while its model has no rows."""

    def __init__(self, view: QAbstractItemView, text: str) -> None:
        super().__init__(view)
        self.view = view
        self.label = secondary_label(text, view.viewport())
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        view.viewport().installEventFilter(self)
        model = view.model()
        if model is not None:
            self._watch(model)
        self.update()

    def _watch(self, model: QAbstractItemModel) -> None:
        for signal in (model.rowsInserted, model.rowsRemoved, model.modelReset):
            signal.connect(self.update)

    def update(self, *_args: object) -> None:
        if not shiboken6.isValid(self.view) or not shiboken6.isValid(self.label):
            return  # the model can outlive its view while both are deleted
        model = self.view.model()
        empty = model is None or model.rowCount() == 0
        self.label.setVisible(empty)
        self.label.setGeometry(self.view.viewport().rect().adjusted(SPACING, 0, -SPACING, 0))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt API)
        if event.type() == QEvent.Type.Resize:
            self.update()
        return False


def set_tab_order(widgets: Sequence[QWidget]) -> None:
    """Tab moves through ``widgets`` in this order (DESIGN.md: reading order)."""
    for first, second in pairwise(widgets):
        QWidget.setTabOrder(first, second)
