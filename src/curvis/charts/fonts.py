"""The chart fonts: the fonts a graph can use, bundled so a graph looks the same on
screen, in its exports and on every computer (src/curvis/charts/font_files/README.md).

Both renderers lay text out with the same line metrics (Matplotlib's: the font's OS/2
typographic ascender, descender and line gap), so each font carries them here. A unit
test reads them back from the font files.

No Qt and no Matplotlib: the chart spec imports this module.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path

FONT_DIR = Path(__file__).parent / "font_files"


@dataclass(frozen=True)
class ChartFont:
    id: str
    """Stored in the chart spec."""
    family: str
    """The family name in the font file (what Qt and Matplotlib call it)."""
    file: str
    """File name in :data:`FONT_DIR`, or "" for Matplotlib's DejaVu Sans."""
    units_per_em: int
    typo_ascender: int
    typo_descender: int
    """Below the baseline, positive."""
    typo_line_gap: int
    digit_advance: float
    """Width of the digit 0, in ems."""
    note: str = ""
    """Shown in the Format dialog's list after the name."""
    generic: str = "sans-serif"
    """The generic family: "sans-serif" or "serif"."""
    fallbacks: tuple[str, ...] = ()
    """Fonts an exported SVG names after this one, for computers without it: fonts with
    the same letter widths, so the layout holds."""

    @property
    def ascent_em(self) -> float:
        """Matplotlib makes a line of text at least this tall above its baseline."""
        return self.typo_ascender / self.units_per_em

    @property
    def descent_em(self) -> float:
        """A line of text reaches at least this far below its baseline."""
        return self.typo_descender / self.units_per_em

    @property
    def line_gap_em(self) -> float:
        """Matplotlib adds half of this above and below each line of a text of several."""
        return self.typo_line_gap / self.units_per_em

    @property
    def char_width_em(self) -> float:
        """Average width of a character, roughly, in ems (to tell what fits)."""
        return 0.62 * self.digit_advance / DEJAVU_DIGIT_ADVANCE

    def path(self) -> Path | None:
        """The font file, or None if Matplotlib's DejaVu Sans cannot be found."""
        if self.file:
            return FONT_DIR / self.file
        return matplotlib_dejavu_path()


DEJAVU_DIGIT_ADVANCE = 0.6362

FONTS: dict[str, ChartFont] = {
    f.id: f
    for f in (
        ChartFont("dejavu_sans", "DejaVu Sans", "", 2048, 1556, 492, 410, DEJAVU_DIGIT_ADVANCE),
        ChartFont(
            "liberation_sans",
            "Liberation Sans",
            "LiberationSans-Regular.ttf",
            2048,
            1491,
            431,
            307,
            0.5562,
            note="the letter widths of Arial",
            fallbacks=("Arial", "Helvetica"),
        ),
        ChartFont(
            "liberation_serif",
            "Liberation Serif",
            "LiberationSerif-Regular.ttf",
            2048,
            1420,
            442,
            307,
            0.5,
            note="the letter widths of Times New Roman",
            generic="serif",
            fallbacks=("Times New Roman", "Times"),
        ),
        ChartFont("inter", "Inter", "Inter-Regular.ttf", 2048, 1984, 494, 0, 0.6309),
    )
}
DEFAULT_FONT = "dejavu_sans"


def chart_font_info(font_id: str) -> ChartFont:
    """The font with ``font_id``; KeyError for an unknown one."""
    return FONTS[font_id]


def matplotlib_dejavu_path() -> Path | None:
    """DejaVu Sans as shipped with Matplotlib, found without importing Matplotlib."""
    spec = importlib.util.find_spec("matplotlib")
    if spec is None or not spec.submodule_search_locations:
        return None
    for location in spec.submodule_search_locations:
        path = Path(location) / "mpl-data" / "fonts" / "ttf" / "DejaVuSans.ttf"
        if path.is_file():
            return path
    return None


__all__ = ("DEFAULT_FONT", "FONTS", "FONT_DIR", "ChartFont", "chart_font_info")
