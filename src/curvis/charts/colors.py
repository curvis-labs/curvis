"""Chart colour schemes.

Chart colours are not theme colours (docs/DESIGN.md, section 3): a graph looks the same
in the light and the dark theme, and when exported. A scheme gives the page, the ink
(axes, text) and the colours that new series get in turn. More schemes and a palette
editor come in step 3.5.
"""

from __future__ import annotations

from dataclasses import dataclass


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


CURVIS = ColorScheme(
    "curvis",
    "Curvis",
    page="#ffffff",
    ink="#1a1a1a",
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

SCHEMES: dict[str, ColorScheme] = {CURVIS.id: CURVIS}
DEFAULT_SCHEME = CURVIS.id


def scheme(scheme_id: str) -> ColorScheme:
    """The scheme with ``scheme_id``, or the default for an unknown id."""
    return SCHEMES.get(scheme_id, CURVIS)
