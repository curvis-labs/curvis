"""Export a graph as PNG, SVG or PDF at the size of its page.

- **PNG** at the resolution asked for (dots per inch, stored in the file).
- **PDF** with DejaVu Sans embedded (TrueType), so it prints the same everywhere.
- **SVG** with the text kept as text in DejaVu Sans, so it stays editable; it looks right
  where that font is installed.

:func:`export_graph` is a module-level function of picklable arguments, so the scheduler
can run it in a worker process (CLAUDE.md rule 5). The file is written to a temporary
name next to the target and then renamed, so a failed export never leaves half a file.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

import numpy as np
import numpy.typing as npt

from curvis.charts.data import ChartData
from curvis.charts.render_mpl.figure import RC_PARAMS, make_figure
from curvis.charts.spec import ChartSpec

DEFAULT_DPI = 300
MIN_DPI, MAX_DPI = 72, 1200


class ExportFormat(StrEnum):
    PNG = "png"
    SVG = "svg"
    PDF = "pdf"

    @property
    def label(self) -> str:
        return {"png": "PNG image", "svg": "SVG drawing", "pdf": "PDF document"}[self.value]


def export_graph(
    spec: ChartSpec,
    data: ChartData | None,
    path: str,
    fmt: ExportFormat | str,
    dpi: float = DEFAULT_DPI,
) -> str:
    """Write ``spec`` drawing ``data`` to ``path``; returns the path written."""
    import matplotlib

    fmt = ExportFormat(fmt)
    if not MIN_DPI <= dpi <= MAX_DPI:
        raise ValueError(f"Choose a resolution from {MIN_DPI} to {MAX_DPI} dpi.")
    target = Path(path)
    metadata: dict[str, Any] = {"Creator": "Curvis"}
    if fmt is ExportFormat.SVG:
        metadata = {"Creator": "Curvis", "Date": None}
    elif fmt is ExportFormat.PDF:
        metadata = {"Creator": "Curvis", "Producer": "Curvis", "CreationDate": None}
    elif fmt is ExportFormat.PNG:
        metadata = {"Software": "Curvis"}
    with matplotlib.rc_context(cast(Any, RC_PARAMS)):
        fig = make_figure(spec, data)
        handle, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        os.close(handle)
        try:
            fig.savefig(
                temporary,
                format=fmt.value,
                dpi=dpi,
                facecolor=fig.get_facecolor(),
                metadata=metadata,
            )
            os.replace(temporary, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise
    return str(target)


def render_image(
    spec: ChartSpec, data: ChartData | None, dpi: float
) -> tuple[npt.NDArray[np.uint8], tuple[float, float, float, float]]:
    """The graph as an RGBA array at ``dpi`` and its plot area (left, top, width, height)
    in pixels from the top left. For the image-diff tests and screenshots."""
    import matplotlib
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    with matplotlib.rc_context(cast(Any, RC_PARAMS)):
        fig = make_figure(spec, data)
        fig.set_dpi(dpi)
        canvas: Any = FigureCanvasAgg(fig)
        canvas.draw()
        image = np.asarray(canvas.buffer_rgba()).copy()
        box = fig.axes[0].get_window_extent()
        height = image.shape[0]
    return image, (float(box.x0), float(height - box.y1), float(box.width), float(box.height))


__all__ = ("DEFAULT_DPI", "ExportFormat", "export_graph", "render_image")
