"""Chart colour schemes (palettes).

Chart colours are not theme colours (docs/DESIGN.md, section 3): a graph looks the same
in the light and the dark theme, and when exported. A scheme gives the page, the ink
(axes, text) and the colours that new series get in turn.

A graph keeps a copy of its scheme in its spec (``ChartSpec.palette``), so a palette the
user edits or deletes later, or a project opened on another computer, never changes it.
The built-in schemes are :data:`SCHEMES`; palettes the user makes with the palette
editor are kept in the Settings (``Settings.palettes``).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, replace

HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
MAX_PALETTE_COLORS = 24
CUSTOM_ID = "custom"
"""Id of a palette that is no built-in scheme (made in the palette editor)."""
USER_PREFIX = "user:"
"""Ids of the palettes the user saved in the Settings start with this."""


def new_palette_id() -> str:
    """A new id for a palette the user saves."""
    return f"{USER_PREFIX}{uuid.uuid4().hex}"


@dataclass(frozen=True)
class ColorScheme:
    id: str
    name: str
    page: str
    """Background of the graph."""
    ink: str
    """Axes, ticks and text."""
    series: tuple[str, ...]
    """Colours of the series, in turn."""

    def series_color(self, index: int) -> str:
        return self.series[index % len(self.series)]

    def same_colors(self, other: ColorScheme) -> bool:
        return (self.page, self.ink, self.series) == (other.page, other.ink, other.series)


def palette_problem(palette: ColorScheme) -> str | None:
    """Why ``palette`` cannot be used, or None."""
    for color in (palette.page, palette.ink, *palette.series):
        if not HEX_COLOR.match(color):
            return f"palette colour {color!r} is not #rrggbb"
    if not 1 <= len(palette.series) <= MAX_PALETTE_COLORS:
        return f"a palette has 1 to {MAX_PALETTE_COLORS} series colours"
    return None


def normalized(palette: ColorScheme) -> ColorScheme:
    """``palette`` with lower-case colours (as the palette editor and the spec keep them)."""
    return replace(
        palette,
        page=palette.page.lower(),
        ink=palette.ink.lower(),
        series=tuple(c.lower() for c in palette.series),
    )


_PAGE, _INK = "#ffffff", "#1a1a1a"

CURVIS = ColorScheme(
    "curvis",
    "Curvis",
    page=_PAGE,
    ink=_INK,
    # The app icon's navy and teal, then colour-blind-safe hues (Okabe-Ito).
    series=(
        "#1d3557",
        "#00a6a6",
        "#e69f00",
        "#cc79a7",
        "#009e73",
        "#d55e00",
        "#56b4e9",
        "#7a7a7a",
    ),
)

COLOR_BLIND_SAFE = ColorScheme(
    "okabe_ito",
    "Color-blind safe",
    page=_PAGE,
    ink=_INK,
    # Okabe & Ito (2008), "Color Universal Design".
    series=("#0072b2", "#e69f00", "#009e73", "#d55e00", "#56b4e9", "#cc79a7", "#f0e442"),
)

BRIGHT = ColorScheme(
    "tol_bright",
    "Bright",
    page=_PAGE,
    ink=_INK,
    # Paul Tol's "bright" scheme, distinct for colour-blind readers too.
    series=("#4477aa", "#ee6677", "#228833", "#ccbb44", "#66ccee", "#aa3377", "#bbbbbb"),
)

PASTEL = ColorScheme(
    "tol_light",
    "Pastel",
    page=_PAGE,
    ink=_INK,
    # Paul Tol's "light" scheme.
    series=("#77aadd", "#ee8866", "#eedd88", "#ffaabb", "#99ddff", "#44bb99", "#bbcc33"),
)

GRAYSCALE = ColorScheme(
    "grayscale",
    "Grayscale",
    page=_PAGE,
    ink=_INK,
    # For print in black and white: greys far enough apart to tell bars from each other.
    series=("#262626", "#8c8c8c", "#cccccc", "#595959", "#a6a6a6", "#e6e6e6"),
)

SCHEMES: dict[str, ColorScheme] = {
    s.id: s for s in (CURVIS, COLOR_BLIND_SAFE, BRIGHT, PASTEL, GRAYSCALE)
}
DEFAULT_SCHEME = CURVIS.id


def scheme(scheme_id: str) -> ColorScheme:
    """The built-in scheme with ``scheme_id``, or the default for an unknown id."""
    return SCHEMES.get(scheme_id, CURVIS)


def recolored(colors: list[str] | tuple[str, ...], palette: ColorScheme) -> tuple[str, ...]:
    """New colours for series coloured ``colors``: series that shared a colour share the
    new one, and distinct colours take the palette's colours in turn (so the colouring
    of a grouped graph, where series of one level share a colour, is kept)."""
    order: dict[str, int] = {}
    for color in colors:
        order.setdefault(color.lower(), len(order))
    return tuple(palette.series_color(order[c.lower()]) for c in colors)


__all__ = (
    "CURVIS",
    "CUSTOM_ID",
    "DEFAULT_SCHEME",
    "MAX_PALETTE_COLORS",
    "SCHEMES",
    "USER_PREFIX",
    "ColorScheme",
    "new_palette_id",
    "normalized",
    "palette_problem",
    "recolored",
    "scheme",
)
