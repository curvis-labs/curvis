"""The themes: every text colour is readable on its background, in light and dark."""

import pytest
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from curvis.ui.theme import (
    BORDER_CONTRAST_MIN,
    CONTRAST_MIN,
    GRID_CONTRAST_MIN,
    TEXT_PAIRS,
    CurvisStyle,
    contrast,
    control_border,
    dark_palette,
    grid_line,
    light_palette,
)


@pytest.mark.parametrize("make", [light_palette, dark_palette], ids=["light", "dark"])
@pytest.mark.parametrize(
    ("text", "background"), TEXT_PAIRS, ids=[f"{t.name}-on-{b.name}" for t, b in TEXT_PAIRS]
)
def test_text_has_enough_contrast(
    make: object, text: QPalette.ColorRole, background: QPalette.ColorRole
) -> None:
    palette = make()  # type: ignore[operator]
    ratio = contrast(palette.color(text), palette.color(background))
    assert ratio >= CONTRAST_MIN, f"{text.name} on {background.name}: {ratio:.2f} : 1"


def test_calculated_columns_stand_out_from_the_table() -> None:
    for palette in (light_palette(), dark_palette()):
        base = palette.color(QPalette.ColorRole.Base)
        calculated = palette.color(QPalette.ColorRole.AlternateBase)
        assert calculated != base
        assert contrast(base, calculated) >= 1.15


def test_contrast_of_black_and_white_is_21() -> None:

    assert contrast(QColor("#000"), QColor("#fff")) == pytest.approx(21.0)
    assert contrast(QColor("#777"), QColor("#777")) == pytest.approx(1.0)


@pytest.mark.parametrize("make", [light_palette, dark_palette], ids=["light", "dark"])
def test_control_outlines_show_on_fields_and_windows(make: object) -> None:
    palette = make()  # type: ignore[operator]
    border = control_border(palette)
    for background in (QPalette.ColorRole.Base, QPalette.ColorRole.Window):
        ratio = contrast(border, palette.color(background))
        assert ratio >= BORDER_CONTRAST_MIN, f"outline on {background.name}: {ratio:.2f} : 1"


@pytest.mark.parametrize("make", [light_palette, dark_palette], ids=["light", "dark"])
def test_table_grid_lines_show_on_cells_and_calculated_columns(make: object) -> None:
    palette = make()  # type: ignore[operator]
    grid = grid_line(palette)
    for background in (QPalette.ColorRole.Base, QPalette.ColorRole.AlternateBase):
        ratio = contrast(grid, palette.color(background))
        assert ratio >= GRID_CONTRAST_MIN, f"grid on {background.name}: {ratio:.2f} : 1"


def test_style_gives_tables_the_grid_line_colour(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QStyle, QStyleOption

    style = CurvisStyle()
    for palette in (light_palette(), dark_palette()):
        option = QStyleOption()
        option.palette = palette
        hint = style.styleHint(QStyle.StyleHint.SH_Table_GridLineColor, option)
        assert hint & 0xFFFFFFFF == grid_line(palette).rgba()


@pytest.mark.parametrize("make", [light_palette, dark_palette], ids=["light", "dark"])
def test_tab_close_button_is_a_cross_that_lights_up_when_hovered(
    qapp: QApplication, make: object
) -> None:
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtWidgets import QStyle, QStyleOption

    palette = make()  # type: ignore[operator]
    window = palette.color(QPalette.ColorRole.Window)

    def draw(state: QStyle.StateFlag) -> QPixmap:
        pixmap = QPixmap(16, 16)
        pixmap.fill(window)
        option = QStyleOption()
        option.palette = palette
        option.rect = QRect(0, 0, 16, 16)
        option.state = QStyle.StateFlag.State_Enabled | state
        painter = QPainter(pixmap)
        CurvisStyle().drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorTabClose, option, painter)
        painter.end()
        return pixmap

    normal = draw(QStyle.StateFlag.State_None).toImage()
    hovered = draw(QStyle.StateFlag.State_MouseOver).toImage()
    assert normal.pixelColor(8, 8) != window  # the cross, through the centre
    assert normal.pixelColor(1, 8) == window  # no box around it
    assert hovered.pixelColor(1, 8) != window  # a soft square when hovered
