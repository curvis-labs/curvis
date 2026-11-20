# Chart fonts

The fonts a graph can use (`curvis.charts.fonts`). They are bundled so a graph looks the
same on screen, in its exports and on every computer, and so the image-diff tests
compare the same letters everywhere. Only the Regular style is needed: charts use no
bold or italic text.

| File | Font | Version | Licence |
| --- | --- | --- | --- |
| (Matplotlib's `mpl-data/fonts/ttf/DejaVuSans.ttf`) | DejaVu Sans | as shipped with Matplotlib | Bitstream Vera / DejaVu (permissive) |
| `LiberationSans-Regular.ttf` | Liberation Sans | 2.1.5 | SIL OFL 1.1, `Liberation-OFL.txt` |
| `LiberationSerif-Regular.ttf` | Liberation Serif | 2.1.5 | SIL OFL 1.1, `Liberation-OFL.txt` |
| `Inter-Regular.ttf` | Inter | 4.1 (`extras/ttf` of the release zip) | SIL OFL 1.1, `Inter-OFL.txt` |

The OFL lets the fonts be bundled with software under any licence (Curvis is GPL-3.0)
as long as the font files stay under the OFL and keep their licence; they are not sold
on their own. Liberation Sans and Serif have the letter widths of Arial and Times New
Roman, so an exported SVG opened where only those are installed keeps its layout.

DejaVu Sans is not copied here: Matplotlib, which Curvis needs for export, ships it.
