"""Compare a graph drawn by the on-screen renderer (pyqtgraph) with its export
(Matplotlib). The method and the thresholds are explained in tests/ui/IMAGE_DIFF.md.

Both renderers draw the page at the same size in pixels. For each **key colour** of the
graph (each series colour and the ink of axes and text) both images get a mask of the
pixels close to that colour. The **miss** of a colour is the share of its pixels in one
image that have no pixel of that colour within :data:`PLOT_RADIUS` (or
:data:`PAGE_RADIUS`) pixels in the other, taken both ways; the result is the largest
miss over the colours. A wrong colour, a missing or extra mark, or a mark in another
place shows as a large miss; antialiasing and a pixel or two of offset do not.

- **plot**: the plot areas (inside the axes) are cut out of both images; the export's
  colour masks are scaled to the size of the screen's plot area, so the same data lands
  on the same pixels.
  This compares points, lines, error bars, bands, bars and boxes.
- **page**: the whole pages, with a wider radius. This compares the layout: titles, tick
  labels, the legend and where the plot sits.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import numpy as np
import numpy.typing as npt
from PIL import Image, ImageFilter
from PySide6.QtGui import QImage

from curvis.charts.colors import scheme
from curvis.charts.data import ChartData
from curvis.charts.render_mpl.export import render_image
from curvis.charts.render_qt.chart_view import render_page
from curvis.charts.spec import ChartSpec

COLOR_DISTANCE = 60
"""A pixel has a key colour when it is nearest to it and closer than this (Euclidean
distance in RGB, out of 441): antialiased blends of two colours count as neither."""
PLOT_RADIUS = 3
"""Pixels (at 192 dpi) a mark may move between the renderers inside the plot area."""
PAGE_RADIUS = 8
"""Pixels (at 192 dpi, about 1 mm) text may move between the renderers on the page."""
MIN_PIXELS = 30
"""A colour with fewer pixels than this in both images is not compared."""
SCALE = 2.0
"""Pages are compared at 192 dpi (96 dpi × 2), as on a high-resolution screen."""

type Box = tuple[float, float, float, float]


@dataclass
class Diff:
    plot: float
    """Largest miss of a key colour in the plot area."""
    page: float
    """Largest miss of a key colour on the whole page."""
    screen: Image.Image
    export: Image.Image


def qimage_to_pil(image: QImage) -> Image.Image:
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = image.width(), image.height()
    raw = np.frombuffer(image.constBits(), dtype=np.uint8, count=image.sizeInBytes())
    array = raw.reshape(height, image.bytesPerLine())[:, : width * 4].reshape(height, width, 4)
    return Image.fromarray(array.copy(), "RGBA").convert("RGB")


def draw_both(spec: ChartSpec, data: ChartData | None) -> tuple[Image.Image, Box, Image.Image, Box]:
    """(screen image, its plot area, export image, its plot area) at :data:`SCALE`."""
    qimage, rect = render_page(spec, data, SCALE)
    screen = qimage_to_pil(qimage)
    array, export_box = render_image(spec, data, 96 * SCALE)
    export = Image.fromarray(array, "RGBA").convert("RGB")
    return screen, (rect.x(), rect.y(), rect.width(), rect.height()), export, export_box


def key_colors(spec: ChartSpec) -> list[tuple[int, int, int]]:
    """The ink and the colours of the shown series."""
    hexes = {scheme(spec.color_scheme).ink, *(s.color for s in spec.series if s.visible)}
    return [_rgb(h) for h in sorted(hexes)]


def _rgb(color: str) -> tuple[int, int, int]:
    return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))


def _masks(
    image: Image.Image, colors: list[tuple[int, int, int]], page: tuple[int, int, int]
) -> list[npt.NDArray[np.bool_]]:
    """For each colour, the pixels nearest to it (of the colours and the page colour) and
    within COLOR_DISTANCE of it. A pixel closer to the mix of two colours than to either
    (an antialiased edge) belongs to none."""
    colors = list(dict.fromkeys(colors))  # a colour given twice is one colour
    pixels = np.asarray(image, dtype=np.float32)
    candidates = np.array([*colors, page], dtype=np.float32)
    distance = np.linalg.norm(pixels[:, :, None, :] - candidates[None, None, :, :], axis=3)
    nearest = np.argmin(distance, axis=2)
    closest = np.min(distance, axis=2)
    blend = np.full(closest.shape, np.inf, dtype=np.float32)
    for i, j in combinations(range(len(candidates)), 2):
        a, b = candidates[i], candidates[j]
        along = b - a
        if not np.any(along):
            continue
        t = np.clip(((pixels - a) @ along) / float(along @ along), 0.2, 0.8)
        mix = a + t[:, :, None] * along
        blend = np.minimum(blend, np.linalg.norm(pixels - mix, axis=2))
    keep = (closest <= COLOR_DISTANCE) & (closest <= blend)
    return [(nearest == i) & keep for i in range(len(colors))]


def _grow(mask: npt.NDArray[np.bool_], radius: int) -> npt.NDArray[np.bool_]:
    image = Image.fromarray(mask.astype(np.uint8) * 255, "L")
    return np.asarray(image.filter(ImageFilter.MaxFilter(2 * radius + 1))) > 0


def miss(
    a: Image.Image,
    b: Image.Image,
    colors: list[tuple[int, int, int]],
    radius: int,
    page: tuple[int, int, int] = (255, 255, 255),
) -> float:
    """The largest share of a key colour's pixels in one image with none of that colour
    within ``radius`` pixels in the other (both ways). The colours are found in each
    image at its own size; ``b``'s masks are then scaled to ``a``'s size."""
    colors = list(dict.fromkeys(colors))
    worst = 0.0
    for mask_a, mask_b in zip(_masks(a, colors, page), _masks(b, colors, page), strict=True):
        if mask_b.shape != mask_a.shape:
            scaled = Image.fromarray(mask_b.astype(np.uint8) * 255, "L").resize(
                (mask_a.shape[1], mask_a.shape[0]), Image.Resampling.NEAREST
            )
            mask_b = np.asarray(scaled) > 0
        if max(mask_a.sum(), mask_b.sum()) < MIN_PIXELS:
            continue
        for one, other in ((mask_a, mask_b), (mask_b, mask_a)):
            missing = int((one & ~_grow(other, radius)).sum())
            worst = max(worst, missing / max(int(one.sum()), MIN_PIXELS))
    return worst


INSET = 3
"""Pixels cut off each side of a plot area: Matplotlib's axes box holds the axis lines,
pyqtgraph's view box does not."""


def crop(image: Image.Image, box: Box) -> Image.Image:
    left, top, width, height = box
    return image.crop(
        (
            round(left) + INSET,
            round(top) + INSET,
            round(left + width) - INSET,
            round(top + height) - INSET,
        )
    )


def compare(spec: ChartSpec, data: ChartData | None) -> Diff:
    screen, screen_box, export, export_box = draw_both(spec, data)
    assert screen.size == export.size, (screen.size, export.size)
    colors = key_colors(spec)
    page_color = _rgb(scheme(spec.color_scheme).page)
    plot_screen = crop(screen, screen_box)
    plot_export = crop(export, export_box)
    plot = miss(plot_screen, plot_export, colors, PLOT_RADIUS, page_color)
    page = miss(screen, export, colors, PAGE_RADIUS, page_color)
    return Diff(plot, page, screen, export)


def save_pair(diff: Diff, folder: Path, name: str) -> Path:
    """Save both images side by side, for a look at a failing comparison."""
    folder.mkdir(parents=True, exist_ok=True)
    a, b = diff.screen, diff.export
    pair = Image.new("RGB", (a.width + b.width + 8, max(a.height, b.height)), (255, 0, 0))
    pair.paste(a, (0, 0))
    pair.paste(b, (a.width + 8, 0))
    path = folder / f"{name}.png"
    pair.save(path)
    return path


def as_array(image: Image.Image) -> npt.NDArray[np.uint8]:
    return np.asarray(image, dtype=np.uint8)
