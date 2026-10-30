"""The themes: every text colour is readable on its background, in light and dark."""

import pytest
from PySide6.QtGui import QPalette

from curvis.ui.theme import CONTRAST_MIN, TEXT_PAIRS, contrast, dark_palette, light_palette


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
    from PySide6.QtGui import QColor

    assert contrast(QColor("#000"), QColor("#fff")) == pytest.approx(21.0)
    assert contrast(QColor("#777"), QColor("#777")) == pytest.approx(1.0)
