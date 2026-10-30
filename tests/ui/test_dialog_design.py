"""Every dialog follows docs/DESIGN.md, in the light and the dark theme.

- It fits its default and its minimum size: no control overlaps another or is cut off.
- Every control has a label (a buddy, its own text) or an accessible name.
- Mnemonics do not clash among the widgets shown at the same time.
- A disabled OK button always shows why.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from itertools import combinations
from pathlib import Path

import pytest
from PySide6.QtCore import QRect, QSettings, QSize
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTabBar,
    QWidget,
)
from pytestqt.qtbot import QtBot

from curvis.compute import Scheduler
from curvis.settings import Settings
from curvis.ui.dialogs import WORKBENCH_MINIMUM, WORKBENCH_SIZE, DialogSkeleton
from curvis.ui.theme import dark_palette, light_palette
from tests.ui.dialog_samples import SAMPLES, Context, settle

# Controls a user reads or operates. Their insides (a spin box's line edit, a tab bar's
# scroll buttons, an item view's viewport) are part of them and not checked on their own.
CONTROLS = (
    QAbstractButton,
    QLabel,
    QLineEdit,
    QComboBox,
    QAbstractSpinBox,
    QAbstractScrollArea,
    QTabBar,
)
COMPOUND = (QComboBox, QAbstractSpinBox, QAbstractScrollArea, QTabBar)
INPUTS = (QLineEdit, QComboBox, QAbstractSpinBox, QAbstractItemView, QPlainTextEdit, QTabBar)

# Buttons that act on a selection in a crowded dialog may go without a mnemonic when
# no letter is free (DESIGN.md, section 4).
NO_MNEMONIC_NEEDED = {"Set role A", "Set role B"}

WORKBENCH = {"import", "calculate", "calculate_paired", "calculate_custom"}


@pytest.fixture
def context(qapp: QApplication, tmp_path: Path) -> Iterator[Context]:
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    ctx = Context(Settings(store), Scheduler(debounce_ms=0))
    yield ctx
    ctx.scheduler.shutdown()


@pytest.fixture(params=["light", "dark"])
def theme(request: pytest.FixtureRequest, qapp: QApplication) -> Iterator[str]:
    original = qapp.palette()
    qapp.setPalette(light_palette() if request.param == "light" else dark_palette())
    yield request.param
    qapp.setPalette(original)


def open_sample(name: str, qtbot: QtBot, context: Context, qapp: QApplication) -> QDialog:
    dialog = SAMPLES[name](context)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    settle(dialog, context, qapp)
    return dialog


def resize(dialog: QDialog, size: QSize, qapp: QApplication) -> None:
    dialog.resize(size)
    for _ in range(3):
        qapp.processEvents()


def shown(dialog: QDialog) -> list[QWidget]:
    """The visible controls of ``dialog``, without the insides of compound controls."""
    widgets: list[QWidget] = []
    for widget in dialog.findChildren(QWidget):
        if not isinstance(widget, CONTROLS) or not widget.isVisibleTo(dialog):
            continue
        if _inside_compound(widget, dialog):
            continue
        if isinstance(widget, QLabel) and not widget.text() and widget.pixmap().isNull():
            continue  # an empty label shows nothing
        widgets.append(widget)
    return widgets


def _inside_compound(widget: QWidget, dialog: QDialog) -> bool:
    parent = widget.parentWidget()
    while parent is not None and parent is not dialog:
        if isinstance(parent, COMPOUND):
            return True
        parent = parent.parentWidget()
    return False


def rect_in_dialog(widget: QWidget, dialog: QDialog) -> QRect:
    return QRect(widget.mapTo(dialog, widget.rect().topLeft()), widget.size())


def problems_with_fit(dialog: QDialog) -> list[str]:
    problems: list[str] = []
    if dialog.minimumSizeHint().boundedTo(dialog.minimumSize()) != dialog.minimumSizeHint():
        problems.append(
            f"the layout needs {dialog.minimumSizeHint().toTuple()}, "
            f"more than the minimum size {dialog.minimumSize().toTuple()}"
        )
    area = dialog.rect()
    widgets = shown(dialog)
    for widget in widgets:
        rect = rect_in_dialog(widget, dialog)
        name = describe(widget)
        if not area.contains(rect):
            problems.append(f"{name} sticks out of the dialog: {rect.getRect()}")
        need = widget.minimumSizeHint()
        if isinstance(widget, QAbstractScrollArea):
            # Lists and editors scroll; they need room for two lines, or their set minimum.
            lines = QSize(40, 2 * widget.fontMetrics().lineSpacing())
            need = widget.minimumSize().expandedTo(lines)
        elif isinstance(widget, QLabel) and widget.wordWrap():
            need = QSize(0, widget.heightForWidth(widget.width()))
        if widget.width() < need.width() or widget.height() < need.height():
            problems.append(f"{name} is cut off: {widget.size().toTuple()} < {need.toTuple()}")
    for a, b in combinations(widgets, 2):
        if a.isAncestorOf(b) or b.isAncestorOf(a):
            continue
        overlap = rect_in_dialog(a, dialog).intersected(rect_in_dialog(b, dialog))
        if overlap.width() > 1 and overlap.height() > 1:
            problems.append(f"{describe(a)} overlaps {describe(b)}")
    return problems


def describe(widget: QWidget) -> str:
    text = ""
    if isinstance(widget, QAbstractButton | QLabel):
        text = widget.text()
    return f"{type(widget).__name__}({text or widget.accessibleName() or widget.objectName()!r})"


def buddies(dialog: QDialog) -> set[int]:
    """Widgets that a label names: through setBuddy or a form row."""
    named = {id(label.buddy()) for label in dialog.findChildren(QLabel) if label.buddy()}
    for form in dialog.findChildren(QFormLayout):
        for row in range(form.rowCount()):
            label = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            field = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
            if label is None or field is None or label.widget() is None:
                continue
            text = label.widget().text() if isinstance(label.widget(), QLabel) else ""
            if text and field.widget() is not None:
                named.add(id(field.widget()))
    return named


def mnemonic(text: str) -> str | None:
    match = re.search(r"(?<!&)&([^&\s])", text)
    return match.group(1).lower() if match else None


def mnemonic_texts(dialog: QDialog) -> list[tuple[str, QWidget]]:
    """Texts that can carry a mnemonic, of the widgets shown."""
    texts: list[tuple[str, QWidget]] = []
    for widget in dialog.findChildren(QWidget):
        if not widget.isVisibleTo(dialog):
            continue
        button = isinstance(widget, QAbstractButton) and not _inside_compound(widget, dialog)
        if button or (isinstance(widget, QLabel) and widget.buddy() is not None):
            texts.append((widget.text(), widget))
        elif isinstance(widget, QGroupBox):
            texts.append((widget.title(), widget))
        elif isinstance(widget, QTabBar):
            texts += [(widget.tabText(i), widget) for i in range(widget.count())]
    return texts


def in_button_box(widget: QWidget) -> bool:
    return isinstance(widget.parentWidget(), QDialogButtonBox)


# --- tests -----------------------------------------------------------------------------


@pytest.mark.parametrize("name", SAMPLES)
@pytest.mark.parametrize("size", ["default", "minimum"])
def test_dialog_fits_without_overlap(
    name: str, size: str, theme: str, qtbot: QtBot, context: Context, qapp: QApplication
) -> None:
    dialog = open_sample(name, qtbot, context, qapp)
    if size == "minimum":
        resize(dialog, dialog.minimumSize().expandedTo(dialog.minimumSizeHint()), qapp)
    problems = problems_with_fit(dialog)
    assert not problems, f"{name} ({theme}, {size} size):\n" + "\n".join(problems)


@pytest.mark.parametrize("name", sorted(WORKBENCH))
def test_workbench_dialogs_have_the_design_sizes(
    name: str, qtbot: QtBot, context: Context, qapp: QApplication
) -> None:
    dialog = open_sample(name, qtbot, context, qapp)
    assert dialog.size() == WORKBENCH_SIZE
    assert dialog.minimumSize() == WORKBENCH_MINIMUM


@pytest.mark.parametrize("name", SAMPLES)
def test_every_control_has_a_label_or_accessible_name(
    name: str, qtbot: QtBot, context: Context, qapp: QApplication
) -> None:
    dialog = open_sample(name, qtbot, context, qapp)
    named = buddies(dialog)
    missing: list[str] = []
    for widget in shown(dialog):
        if isinstance(widget, QAbstractButton):
            if not (widget.text() or widget.accessibleName()):
                missing.append(describe(widget))
        elif isinstance(widget, INPUTS) and not (id(widget) in named or widget.accessibleName()):
            missing.append(describe(widget))
    assert not missing, f"{name}: no label or accessible name for {missing}"


@pytest.mark.parametrize("name", SAMPLES)
def test_mnemonics_are_unique_and_present(
    name: str, qtbot: QtBot, context: Context, qapp: QApplication
) -> None:
    dialog = open_sample(name, qtbot, context, qapp)
    seen: dict[str, str] = {}
    clashes: list[str] = []
    missing: list[str] = []
    for text, widget in mnemonic_texts(dialog):
        letter = mnemonic(text)
        if letter is None:
            names_a_field = not isinstance(widget, QLabel) or isinstance(widget.buddy(), INPUTS)
            needs_one = (
                isinstance(widget, QAbstractButton | QLabel)
                and names_a_field
                and not in_button_box(widget)
                and any(c.isalpha() for c in text)
                and text.replace("&", "") not in NO_MNEMONIC_NEEDED
            )
            if needs_one:
                missing.append(text)
            continue
        if letter in seen:
            clashes.append(f"'{text}' and '{seen[letter]}' both use {letter.upper()}")
        seen[letter] = text
    assert not clashes, f"{name}: " + "; ".join(clashes)
    assert not missing, f"{name}: no mnemonic on {missing}"


@pytest.mark.parametrize("name", SAMPLES)
def test_a_disabled_ok_says_why(
    name: str, qtbot: QtBot, context: Context, qapp: QApplication
) -> None:
    dialog = open_sample(name, qtbot, context, qapp)
    skeleton = getattr(dialog, "skeleton", None)
    assert isinstance(skeleton, DialogSkeleton), f"{name} does not use the dialog skeleton"
    assert skeleton.reason_shown()


def test_calculate_says_why_ok_is_disabled_under_the_formula(
    qtbot: QtBot, context: Context, qapp: QApplication
) -> None:
    dialog = open_sample("calculate_custom", qtbot, context, qapp)
    skeleton: DialogSkeleton = dialog.skeleton  # type: ignore[attr-defined]
    assert skeleton.ok_button is not None and not skeleton.ok_button.isEnabled()
    assert dialog.formula_error.text()  # type: ignore[attr-defined]
    assert not skeleton.feedback.text()  # shown once, under the field that causes it
    assert skeleton.reason_shown()


def test_the_checks_find_overlaps_cut_offs_and_clashes(qtbot: QtBot) -> None:
    dialog = QDialog()
    qtbot.addWidget(dialog)
    dialog.resize(300, 200)
    first = QPushButton("&Save", dialog)
    second = QPushButton("&Sort", dialog)
    first.setGeometry(10, 10, 100, 30)
    second.setGeometry(50, 20, 100, 30)
    tiny = QPushButton("A long button text", dialog)
    tiny.setGeometry(10, 100, 20, 30)
    dialog.show()
    problems = problems_with_fit(dialog)  # type: ignore[arg-type]
    assert any("overlaps" in p for p in problems)
    assert any("cut off" in p for p in problems)
    letters = [mnemonic(text) for text, _ in mnemonic_texts(dialog)]  # type: ignore[arg-type]
    assert letters.count("s") == 2
