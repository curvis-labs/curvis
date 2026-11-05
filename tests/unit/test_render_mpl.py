"""The export renderer: every graph type, the three formats, resolution and fonts."""

import re
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from curvis.charts.builder import default_spec
from curvis.charts.data import compute, gather
from curvis.charts.render_mpl.export import ExportFormat, export_graph, render_image
from curvis.charts.spec import ChartFamily, ChartVariant
from tests.unit.charts_tables import column_table, replicate_table


def xy():  # type: ignore[no-untyped-def]
    table = replicate_table()
    spec = default_spec(table, ChartFamily.XY)
    return spec, compute(gather(table, spec, {}))


def bars(variant: ChartVariant):  # type: ignore[no-untyped-def]
    table = column_table()
    spec = replace(default_spec(table, ChartFamily.BAR), variant=variant)
    return spec, compute(gather(table, spec, {}))


@pytest.mark.parametrize("variant", [ChartVariant.SCATTER, ChartVariant.LINE])
def test_xy_variants_render(variant: ChartVariant) -> None:
    spec, data = xy()
    image, box = render_image(replace(spec, variant=variant), data, 96)
    assert image.shape == (226, 340, 4)
    left, top, width, height = box
    assert 0 < left < left + width < 340 and 0 < top < top + height < 226


@pytest.mark.parametrize("variant", [ChartVariant.BAR, ChartVariant.BOX])
def test_bar_variants_render(variant: ChartVariant) -> None:
    spec, data = bars(variant)
    image, _box = render_image(spec, data, 96)
    assert image.shape[2] == 4


def test_unsupported_variants_say_so() -> None:
    spec, data = bars(ChartVariant.BAR)
    with pytest.raises(ValueError, match="cannot be exported"):
        render_image(replace(spec, variant=ChartVariant.VIOLIN), data, 96)


def test_png_has_the_page_size_and_resolution(tmp_path: Path) -> None:
    spec, data = xy()
    path = Path(export_graph(spec, data, str(tmp_path / "g.png"), ExportFormat.PNG, 300))
    with Image.open(path) as image:
        assert image.size == (1062, 708)  # 9 x 6 cm at 300 dpi
        assert image.info["dpi"] == pytest.approx((300, 300), abs=0.01)


def test_pdf_embeds_the_font(tmp_path: Path) -> None:
    spec, data = xy()
    raw = Path(export_graph(spec, data, str(tmp_path / "g.pdf"), "pdf")).read_bytes()
    assert raw.startswith(b"%PDF")
    assert b"/FontFile2" in raw  # an embedded TrueType font
    assert b"DejaVuSans" in raw
    assert b"/Type3" not in raw
    # 9 x 6 cm in points
    box = re.search(rb"/MediaBox \[\s*0 0 ([\d.]+) ([\d.]+)\s*\]", raw)
    assert box is not None
    assert float(box.group(1)) == pytest.approx(9 / 2.54 * 72, abs=0.01)
    assert float(box.group(2)) == pytest.approx(6 / 2.54 * 72, abs=0.01)


def test_svg_keeps_text_as_text(tmp_path: Path) -> None:
    spec, data = xy()
    text = Path(export_graph(spec, data, str(tmp_path / "g.svg"), "svg")).read_text()
    assert "<text" in text and "DejaVu Sans" in text
    assert ">Assay<" in text and ">Control<" in text
    assert 'width="255.118' in text  # 9 cm in points


def test_the_same_graph_gives_the_same_svg(tmp_path: Path) -> None:
    spec, data = xy()
    first = Path(export_graph(spec, data, str(tmp_path / "a.svg"), "svg")).read_bytes()
    second = Path(export_graph(spec, data, str(tmp_path / "b.svg"), "svg")).read_bytes()
    assert first == second


def test_hidden_series_and_legend_flags(tmp_path: Path) -> None:
    spec, data = xy()
    hidden = spec.with_series_visible(spec.series[0].source, False)
    text = Path(export_graph(hidden, data, str(tmp_path / "h.svg"), "svg")).read_text()
    assert ">Control<" not in text and ">Drug<" in text
    quiet = replace(spec, series=(replace(spec.series[0], in_legend=False), spec.series[1]))
    text = Path(export_graph(quiet, data, str(tmp_path / "q.svg"), "svg")).read_text()
    assert ">Control<" not in text
    none = replace(spec, show_legend=False)
    text = Path(export_graph(none, data, str(tmp_path / "n.svg"), "svg")).read_text()
    assert ">Drug<" not in text


def test_a_failed_export_leaves_no_file(tmp_path: Path) -> None:
    spec, data = xy()
    with pytest.raises(ValueError, match="resolution"):
        export_graph(spec, data, str(tmp_path / "g.png"), "png", 5000)
    target = tmp_path / "existing.png"
    target.write_bytes(b"old")
    with pytest.raises(ValueError):
        export_graph(replace(spec, variant=ChartVariant.LINE), None, str(target), "tiff")
    assert target.read_bytes() == b"old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["existing.png"]


def test_matplotlib_is_not_imported_at_start_up() -> None:
    import subprocess
    import sys

    code = (
        "import sys, curvis.charts.axes, curvis.charts.render_mpl.figure, "
        "curvis.charts.render_mpl.export; print('matplotlib' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
