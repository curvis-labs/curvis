# Image-diff tests: screen vs. export

`tests/ui/test_image_diff.py` checks that an exported graph (Matplotlib,
`curvis.charts.render_mpl`) matches the Graph panel (pyqtgraph,
`curvis.charts.render_qt`). It draws every graph type (scatter, line with a fitted curve and
its band, bar, box, interaction, and survival with its bands and censor ticks), with and without a legend, in the light and the dark app theme,
plus layouts that once differed: titles wider than the plot and turned category labels.

Step 3.5 added the rest of the Bar family: horizontal bars, violins (trimmed, and not
trimmed with individual values), column scatter, histograms (automatic bins and bins of a
set width), bars and boxes with individual values, and grouped bars (by series and by
category), grouped horizontal bars, boxes and violins; a graph in a palette of its own; and
each bundled chart font (DejaVu Sans, Liberation Sans, Liberation Serif, Inter) on an XY
graph with a fit, a grouped bar graph and a histogram, each with a title long enough to
wrap. Both renderers draw bar-family graphs from the same shapes
(`curvis.charts.marks`), so these differ only where text does.
The helpers are in `tests/ui/image_diff.py`.

## Method

Both renderers draw the graph's page at the same size in pixels, at 192 dpi (96 dpi × 2,
like a high-resolution screen). They share the page size, view ranges, ticks, font
(the graph's chart font, loaded from the same bundled file by both, with the same line
metrics: `curvis.charts.fonts`), line widths and legend sizes (`curvis.charts.axes`), so
the same data should land on the same pixels.

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

| Measure | What is compared | Radius | Threshold | Measured (step 2.8 cases) | Measured (step 3.5 cases) |
| --- | --- | --- | --- | --- | --- |
| **plot** | The plot area inside the axes, cut out of each image (3 px inset, because Matplotlib's axes box holds the axis lines and pyqtgraph's view box does not). The export's colour masks are scaled to the size of the screen's plot area. | 3 px (0.4 mm) | 0.01 | 0 – 0.0048 | 0 – 0.0022 |
| **page** | The whole page. | 8 px (1 mm) | 0.12 | 0 – 0.037 | 0 – 0.051 |

The 99 comparisons of step 3.5 (68 of them new) are on Linux with Qt's offscreen platform. The
plot measures above 0 come from symbols: the interaction graph's with a legend (0.0048,
as before step 3.5) and individual values on bars (0.0022), where a symbol's antialiased
rim on a coloured bar falls between two key colours. The largest page measures (0.049 to
0.051) are box plots and violins with a legend. They come from the legend of patches,
which sits 3 px (at 192 dpi) lower on screen than on export in every bar-family graph,
as it did in step 2.8 (bar 0.023, box 0.037); a box plot's or violin's own marks are
light, so the legend's patches and names are a larger share of its key-colour pixels.
Not yet fixed; well under the threshold.

The thresholds were set after the measurements, with a margin. The control test
(`test_the_measure_sees_a_wrong_colour_and_a_wrong_range`) shows that the plot measure
exceeds five times its threshold when a series has another colour, the Y range changes,
or a series is hidden. `test_the_app_theme_does_not_change_a_graph` checks that both
renderers give identical pixels in the light and the dark theme: chart colours never come
from the theme (DESIGN.md §3).

Fonts have two controls. `test_the_measure_sees_a_wrong_font` draws the screen in DejaVu
Sans and the export in Liberation Serif or Liberation Sans: the page measure is then 0.29
and 0.18, above its threshold. Inter is so close to DejaVu Sans in width and shape that
the page measure would not see a swap (0.048), so
`test_both_renderers_use_the_graphs_font` checks what each renderer resolves the font
to: Qt's `QFontInfo` family and the file Matplotlib's font manager picks (the bundled
one, also where another version of the font is installed).

## What still differs (page measure)

- **Tick labels at the ends of the X axis.** Both renderers make the plot narrower so a
  label at the end of the axis (e.g. "100") fits, also when a legend is to the right. The
  plots can still differ in width by a few pixels (Qt's page is cut to whole pixels, and
  text advances differ by a fraction of a pixel).
- **Where a long title breaks.** It follows from the plot's width, so a title whose break
  falls within a pixel or two of the plot's edge can break one word apart on screen and
  in the export (with the default size, "Response of cells to dose" on a square page
  with a legend: its second line fits the export's plot by 0.75 px).
- **Text rasterising.** Qt and Matplotlib draw the chart fonts with slightly different
  hinting and kerning; titles and labels can sit a pixel or two apart. With a long title
  that wraps, the font cases measure 0 – 0.040 on the page (the largest: DejaVu Sans on
  grouped bars; Liberation Serif at most 0.032).
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
- **Title and text lines.** pyqtgraph's own title row is 30 px high at any zoom and as
  wide as its text, so a long title widened the plot and squeezed the legend, and the
  screen plot sat about 9 px higher than the export's. The Graph panel now has its own
  title item (`ChartTitle`) laid out as Matplotlib lays out `Axes.set_title`: baseline
  `FONT_ASCENT_EM` under the page padding, plot `TITLE_PAD_PT` under the last baseline. A
  title wider than the plot is broken into lines between words in both renderers
  (`wrap_to_fit`), 1.2 em apart (`FONT_LINE_GAP_EM`). The export wraps it while the
  constrained layout runs, and runs the layout again while the lines do not fit the
  plot's final width. Axis titles and X tick labels, level or turned, take Matplotlib's
  1 em line (`FONT_ASCENT_EM + FONT_DESCENT_EM`) instead of Qt's 1.16 em one.
  `test_title_and_turned_labels_match` covers long titles on a square page with a legend
  and turned category labels on square and wide pages (bars, and the interaction graph,
  which no longer needs a tall page).
- **Legend spacing.** Matplotlib's constrained layout puts the legend's frame against the
  page edge and, before it, the room for a tick label reaching past the end of the X
  axis and three paddings (the plot's, and the legend's on both its sides); the legend has
  a border inside. The screen legend has the same margins (`PAGE_PAD_PT`,
  `LEGEND_BORDER_EM`). Before, the screen put one padding to the right of the legend and
  let the end label reach into the gap, so its legend sat about 7 px further left.

When a comparison fails, both images are saved side by side in `build/image-diff/` (CI
uploads them as an artifact), and the failure message says where each key colour's pixels
are on screen and in the export, so it can be read from the log alone.
