"""The bundled chart fonts: their files, licences and line metrics."""

from __future__ import annotations

import pytest

from curvis.charts.fonts import FONT_DIR, FONTS, chart_font_info


@pytest.mark.parametrize("font_id", sorted(FONTS))
def test_metrics_are_those_of_the_font_file(font_id: str) -> None:
    from matplotlib.ft2font import FT2Font

    font = chart_font_info(font_id)
    path = font.path()
    assert path is not None and path.is_file()
    ft = FT2Font(str(path))
    assert ft.family_name == font.family
    os2 = ft.get_sfnt_table("OS/2")
    assert os2 is not None
    assert ft.get_sfnt_table("head")["unitsPerEm"] == font.units_per_em
    assert (os2["sTypoAscender"], -os2["sTypoDescender"], os2["sTypoLineGap"]) == (
        font.typo_ascender,
        font.typo_descender,
        font.typo_line_gap,
    )
    ft.set_size(1000, 72)
    advance = ft.load_char(ord("0")).linearHoriAdvance / 65536 / 1000
    assert advance == pytest.approx(font.digit_advance, abs=5e-4)


def test_bundled_fonts_keep_their_licences() -> None:
    bundled = {f.file for f in FONTS.values() if f.file}
    assert bundled == {p.name for p in FONT_DIR.glob("*.ttf")}
    for licence in ("Liberation-OFL.txt", "Inter-OFL.txt"):
        text = (FONT_DIR / licence).read_text(encoding="utf-8")
        assert "SIL Open Font License" in text or "SIL OPEN FONT LICENSE" in text
    readme = (FONT_DIR / "README.md").read_text(encoding="utf-8")
    for font in FONTS.values():
        assert font.family in readme


def test_matplotlib_uses_the_bundled_files() -> None:
    from matplotlib import font_manager

    from curvis.charts.render_mpl.figure import _register_fonts

    _register_fonts()
    for font in FONTS.values():
        found = font_manager.findfont(
            font_manager.FontProperties(family=font.family), fallback_to_default=False
        )
        assert found == str(font.path())


def test_export_names_the_graph_font_and_its_fallbacks() -> None:
    from dataclasses import replace

    from curvis.charts.builder import default_spec
    from curvis.charts.render_mpl.figure import chart_rc
    from curvis.charts.spec import ChartFamily
    from tests.unit.charts_tables import column_table

    spec = replace(default_spec(column_table(), ChartFamily.BAR), font="liberation_serif")
    rc = chart_rc(spec)
    assert rc["font.family"] == "serif"
    assert rc["font.serif"] == ["Liberation Serif", "Times New Roman", "Times"]
