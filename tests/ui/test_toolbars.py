"""The main and table toolbars (docs/DESIGN.md, section 7)."""

from collections.abc import Iterator

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QToolBar, QToolButton
from pytestqt.qtbot import QtBot

from curvis.charts.builder import NEEDS_SURVIVAL, NEEDS_TWO_WAY, NEEDS_X
from curvis.core import AddTable, SetCellValue
from curvis.settings import Settings
from curvis.ui.main_window import (
    NO_PROJECT,
    NOTHING_TO_REDO,
    NOTHING_TO_UNDO,
    SELECT_FOR_CATEGORY,
    SELECT_TO_GROUP,
    MainWindow,
)
from curvis.ui.theme import CurvisStyle, dark_palette, light_palette
from curvis.ui.toolbar_icons import icon_colors, toolbar_icon
from curvis.ui.toolbars import MAIN_BUTTONS, TABLE_BUTTONS
from tests.unit.charts_tables import replicate_table


@pytest.fixture(autouse=True)
def curvis_style(qapp: QApplication) -> Iterator[None]:
    """The app always runs with CurvisStyle and a Curvis palette (ThemeManager), which
    size the buttons and draw the Plot arrow; tests must not depend on another test
    having set them."""
    old_style, old_palette = qapp.style().name(), qapp.palette()
    qapp.setStyle(CurvisStyle())
    qapp.setPalette(light_palette())
    yield
    qapp.setStyle(old_style)
    qapp.setPalette(old_palette)


MAIN_IDS = [b.action_id for b in MAIN_BUTTONS if b is not None]
TABLE_IDS = [b.action_id for b in TABLE_BUTTONS if b is not None]


def buttons(toolbar: QToolBar) -> list[QAction]:
    return [a for a in toolbar.actions() if not a.isSeparator()]


def table_toolbar(window: MainWindow) -> QToolBar:
    assert window.table_toolbar is not None
    return window.table_toolbar


def test_every_toolbar_button_is_a_registered_action(window: MainWindow) -> None:
    window.new_document()
    for toolbar in (window.main_toolbar, table_toolbar(window)):
        for action in buttons(toolbar):
            assert action in window.registry, action.text()
            assert action.icon() is not None and not action.icon().isNull()
    assert [a.objectName() for a in buttons(window.main_toolbar)] == MAIN_IDS
    assert [a.objectName() for a in buttons(table_toolbar(window))] == TABLE_IDS


def test_toolbars_are_fixed_and_out_of_the_tab_order(window: MainWindow) -> None:
    window.new_document()
    main, table = window.main_toolbar, table_toolbar(window)
    for toolbar in (main, table):
        assert not toolbar.isMovable() and not toolbar.isFloatable()
        for action in buttons(toolbar):
            widget = toolbar.widgetForAction(action)
            assert widget.focusPolicy() == Qt.FocusPolicy.NoFocus
            assert widget.height() >= 28
    assert window.toolBarArea(main) == window.toolBarArea(table)
    assert not window.toolBarBreak(table)  # one row
    assert main.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    assert table.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextBesideIcon


def test_labels_and_tooltips(window: MainWindow) -> None:
    window.new_document()
    a = window.registry.action
    assert a("file.save").toolTip() == f"Save project ({a('file.save').shortcut().toString()})"
    assert a("table.group").iconText() == "Group as replicates"  # no "&" or "…"
    assert a("table.calculate").toolTip() == "Calculate (Ctrl+Shift+K)"
    assert a("file.import").iconText() == "Import data"
    assert [a(i).iconText() for i in TABLE_IDS] == [
        "Import data",
        "Add column",
        "Group as replicates",
        "Set category",
        "Calculate",
        "Plot",
        "Analyze",
    ]


def disabled_reasons(window: MainWindow) -> dict[str, str]:
    toolbars = [window.main_toolbar]
    if window.table_toolbar is not None:
        toolbars.append(window.table_toolbar)
    return {
        a.objectName(): a.toolTip()
        for toolbar in toolbars
        for a in buttons(toolbar)
        if not a.isEnabled()
    }


def assert_reasons_given(window: MainWindow) -> dict[str, str]:
    reasons = disabled_reasons(window)
    for action_id, tip in reasons.items():
        assert tip, f"{action_id} is disabled without saying why"
        assert "(" not in tip, f"{action_id} shows its name instead of why: {tip!r}"
    return reasons


def test_disabled_buttons_say_why(window: MainWindow, qtbot: QtBot) -> None:
    reasons = assert_reasons_given(window)  # the launcher
    assert reasons["file.save"] == NO_PROJECT
    assert window.table_toolbar is None or not window.table_toolbar.isVisible()

    tab = window.new_document()
    reasons = assert_reasons_given(window)
    assert reasons["edit.undo"] == NOTHING_TO_UNDO
    assert reasons["edit.redo"] == NOTHING_TO_REDO
    assert "file.save" not in reasons

    table = tab.document.tables[0]
    tab.document.execute(SetCellValue(table.id, table.columns[1].id, 0, 1))
    assert window.registry.action("edit.undo").toolTip().startswith("Undo (")
    tab.document.undo()
    assert window.registry.action("edit.redo").toolTip().startswith("Redo (")

    # A spare column (right of the data) is current: nothing to group.
    assert tab.table_view is not None
    model = tab.table_view.model()
    tab.table_view.setCurrentIndex(model.index(0, model.n_data_columns + 1))
    tab.table_view.clearSelection()
    reasons = assert_reasons_given(window)
    assert reasons["table.group"] == SELECT_TO_GROUP
    assert reasons["table.set_category"] == SELECT_FOR_CATEGORY
    tab.table_view.setCurrentIndex(model.index(0, 1))
    assert "table.group" not in disabled_reasons(window)


def test_table_toolbar_shows_only_with_a_table(window: MainWindow) -> None:
    tab = window.new_document()
    assert table_toolbar(window).isVisible()
    window.close_tab(tab)
    assert not table_toolbar(window).isVisible()
    assert window.main_toolbar.isVisible()


def test_plot_menu_lists_families_and_says_why_some_are_disabled(
    window: MainWindow, qtbot: QtBot
) -> None:
    window.new_document()  # a blank table: X and Y columns without values
    a = window.registry.action
    plot = a("graph.plot")
    menu = plot.menu()
    assert isinstance(menu, QMenu)
    assert [x.objectName() for x in menu.actions()] == [
        "graph.new.xy",
        "graph.new.bar",
        "graph.new.survival",
        "graph.new.interaction",
    ]
    assert not a("graph.new.xy").isEnabled()
    assert a("graph.new.xy").text() == f"&XY\t{NEEDS_X}"
    assert a("graph.new.xy").toolTip() == NEEDS_X
    assert a("graph.new.bar").isEnabled()
    assert a("graph.new.survival").toolTip() == NEEDS_SURVIVAL
    assert a("graph.new.interaction").toolTip() == NEEDS_TWO_WAY
    entry = next(e for e in window.registry.entries() if e.id == "graph.new.xy")
    assert entry.text == "XY"  # the palette shows the name, not the reason

    button = table_toolbar(window).widgetForAction(plot)
    assert isinstance(button, QToolButton)
    assert button.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup
    seen: list[bool] = []

    def close_menu() -> None:
        seen.append(menu.isVisible())
        menu.hide()

    QTimer.singleShot(100, close_menu)
    plot.trigger()  # from the menu bar or the palette: opens the family menu
    qtbot.waitUntil(lambda: bool(seen))
    assert seen == [True]


def test_families_enable_for_a_table_with_xy_data(window: MainWindow) -> None:
    tab = window.new_document()
    tab.document.execute(AddTable(replicate_table(), 0))
    a = window.registry.action
    assert a("graph.new.xy").isEnabled() and a("graph.new.xy").text() == "&XY"
    assert a("graph.new.xy").toolTip() == "XY"


def test_narrow_window_moves_buttons_into_the_overflow_menu(
    window: MainWindow, qtbot: QtBot
) -> None:
    window.new_document()
    window.resize(560, 500)
    qtbot.waitUntil(lambda: window.width() <= 600)
    QApplication.processEvents()
    extension = table_toolbar(window).findChild(QToolButton, "qt_toolbar_ext_button")
    assert extension is not None and extension.isVisible()


def test_icons_take_their_colours_from_the_palette(qtbot: QtBot) -> None:
    for palette in (light_palette(), dark_palette()):
        obj, act = icon_colors(palette, QIcon.Mode.Normal)
        assert obj == palette.color(palette.ColorGroup.Active, palette.ColorRole.ButtonText).name()
        assert act == palette.color(palette.ColorGroup.Active, palette.ColorRole.Link).name()
        dis = icon_colors(palette, QIcon.Mode.Disabled)
        assert dis[0] == dis[1] != obj
        hl = icon_colors(palette, QIcon.Mode.Active)
        assert hl[0] == hl[1] == palette.color(palette.ColorRole.HighlightedText).name()

    app = QApplication.instance()
    assert isinstance(app, QApplication)
    old = app.palette()
    try:
        icon = toolbar_icon("plot")
        colors = {}
        for name, palette in (("light", light_palette()), ("dark", dark_palette())):
            app.setPalette(palette)
            image = icon.pixmap(40, 40).toImage()
            opaque = [
                image.pixelColor(x, y)
                for x in range(image.width())
                for y in range(image.height())
                if image.pixelColor(x, y).alpha() == 255
            ]
            assert opaque, "the icon drew nothing"
            colors[name] = {c.name() for c in opaque}
            link = palette.color(palette.ColorRole.Link).name()
            assert link in colors[name] or _near(opaque, QColor(link))
        assert colors["light"] != colors["dark"]  # one file, recoloured per theme
    finally:
        app.setPalette(old)


def _near(colors: list[QColor], target: QColor) -> bool:
    return any(
        abs(c.red() - target.red())
        + abs(c.green() - target.green())
        + abs(c.blue() - target.blue())
        < 12
        for c in colors
    )


def test_plot_arrow_sits_beside_the_text(window: MainWindow) -> None:
    window.new_document()
    QApplication.processEvents()
    button = table_toolbar(window).widgetForAction(window.registry.action("graph.plot"))
    image = button.grab().toImage()
    arrow = button.palette().color(button.palette().ColorRole.PlaceholderText)
    text_end = 6 + 20 + 4 + button.fontMetrics().horizontalAdvance("Plot")
    marks = [
        (x, y)
        for x in range(text_end, image.width())
        for y in range(image.height())
        if _near([image.pixelColor(x, y)], arrow)
    ]
    assert marks, "no menu arrow in the secondary text colour after the text"
    xs, ys = [x for x, _ in marks], [y for _, y in marks]
    assert min(xs) <= text_end + 6  # just after "Plot"
    assert abs((min(ys) + max(ys)) / 2 - image.height() / 2) <= 2  # vertically centred
    background = image.pixelColor(1, 1)
    corner = [
        image.pixelColor(x, y)
        for x in range(image.width() - 6, image.width())
        for y in range(image.height() - 6, image.height())
    ]
    assert all(c == background for c in corner)  # not in the corner, as Fusion draws it


def test_main_toolbar_is_made_at_first_show_and_comes_first(
    qtbot: QtBot, settings: Settings
) -> None:
    """Building the window shell stays quick: the main toolbar is made when the window
    is first shown (before its first paint), and stays left of the table toolbar even
    when a table toolbar was made first."""
    hidden = MainWindow(settings)
    qtbot.addWidget(hidden)
    assert hidden.main_toolbar is None
    hidden.new_document()  # makes the table toolbar before the window is shown
    hidden.show()
    qtbot.waitExposed(hidden)
    main, table = hidden.main_toolbar, hidden.table_toolbar
    assert main is not None and table is not None
    assert main.isVisible()
    assert main.geometry().left() < table.geometry().left()
    assert [a.objectName() for a in buttons(main)] == MAIN_IDS
    if hidden._scheduler is not None:
        hidden._scheduler.shutdown()
