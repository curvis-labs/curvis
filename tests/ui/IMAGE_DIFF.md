# Image-diff tests: screen vs. export

`tests/ui/test_image_diff.py` checks that an exported graph (Matplotlib,
`curvis.charts.render_mpl`) matches the Graph panel (pyqtgraph,
`curvis.charts.render_qt`). It draws every graph type (scatter, line with a fitted curve and
its band, bar, box), with and without a legend, in the light and the dark app theme.
The helpers are in `tests/ui/image_diff.py`.

## Method

Both renderers draw the graph's page at the same size in pixels, at 192 dpi (96 dpi × 2,
like a high-resolution screen). They share the page size, view ranges, ticks, font
(DejaVu Sans), line widths and legend sizes (`curvis.charts.axes`), so the same data
should land on the same pixels.

A plain pixel difference does not work well here: most of a graph is white, so a series
drawn in the wrong colour changes only a few per cent of the pixels, while antialiasing
and a one-pixel offset change many. The test therefore compares **colours where they
are**:

1. The **key colours** are the ink (axes, text) and the colour of each shown series.
2. In each image, every pixel is given the key colour (or the page colour) it is nearest
   to, if it is within a distance of 60 (Euclidean, in RGB). A pixel nearer to the mix of
   two colours than to either (an antialiased edge) gets none.
3. The **miss** of a colour is the share of its pixels in one image that have no pixel of
   that colour within a radius in the other image, taken both ways. The result is the
   largest miss over the colours. Colours with fewer than 30 pixels are skipped.

Two measures:

| Measure | What is compared | Radius | Threshold | Measured (all 16 cases) |
| --- | --- | --- | --- | --- |
| **plot** | The plot area inside the axes, cut out of each image (3 px inset, because Matplotlib's axes box holds the axis lines and pyqtgraph's view box does not). The export's colour masks are scaled to the size of the screen's plot area. | 3 px (0.4 mm) | 0.01 | 0.0000 |
| **page** | The whole page. | 8 px (1 mm) | 0.12 | 0.004 – 0.039 |

The thresholds were set after the measurements, with a margin. The control test
(`test_the_measure_sees_a_wrong_colour_and_a_wrong_range`) shows that the plot measure
exceeds five times its threshold when a series has another colour, the Y range changes,
or a series is hidden. `test_the_app_theme_does_not_change_a_graph` checks that both
renderers give identical pixels in the light and the dark theme: chart colours never come
from the theme (DESIGN.md §3).

## What still differs (page measure)

- **Tick labels at the ends of the X axis.** Matplotlib makes the plot narrower so a label
  at the end of the axis (e.g. "100") fits; pyqtgraph lets it reach past the end. This can
  move the points near the right end by a millimetre or two.
- **Text rasterising.** Qt and Matplotlib draw DejaVu Sans with slightly different hinting
  and kerning; titles and labels can sit a pixel or two apart.
- **Hidden series** (legend click) are left out of an exported legend; the screen lists
  them dimmed so they can be clicked back on. The tests compare graphs with every series
  shown.

## Pitfalls found on the CI runners

- **Subpixel text.** Where the system asks for subpixel (LCD) antialiasing, Qt drew chart
  text with coloured fringes that the measure counted as series colour (page 0.21). Chart
  fonts now always use grey antialiasing, as Matplotlib does (`chart_font`). On Windows
  with ClearType, Qt's raster engine ignores that for an opaque target, so `render_page`
  paints into a non-premultiplied ARGB32 image, where it always draws grey text. A
  Windows screen may still show chart text with ClearType: a display effect only.
- **Legend spacing.** Matplotlib's layout pads both sides of the gap between the plot and
  the legend, and the legend has a border inside; the screen legend now has the same
  margins (`PAGE_PAD_PT`, `LEGEND_BORDER_EM`), so the two plots are equally wide.

When a comparison fails, both images are saved side by side in `build/image-diff/` (CI
uploads them as an artifact), and the failure message says where each key colour's pixels
are on screen and in the export, so it can be read from the log alone.
