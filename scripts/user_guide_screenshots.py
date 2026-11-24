"""Make the screenshots of the user guide (docs/user-guide/images), from the real app.

    uv run python scripts/user_guide_screenshots.py

Every picture is drawn offscreen in the light theme, at twice the pixel density so it
stays sharp on high-resolution screens. The dialogs use the sample data of
tests/ui/dialog_samples.py; the main window opens the sample projects of
curvis.samples. Run it again after a change to a screen the guide shows; the page that
uses a picture is named in IMAGES.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "2")
sys.path.insert(0, str(Path(__file__).parents[1]))

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QWidget
from tests.ui.dialog_samples import SAMPLES, Context, settle, two_way_document

from curvis.compute import Scheduler
from curvis.settings import Settings, Theme
from curvis.ui.main_window import MainWindow, SaveChoice
from curvis.ui.theme import ThemeManager

OUT = Path(__file__).parents[1] / "docs" / "user-guide" / "images"

DIALOGS = {
    # picture: dialog sample (tests/ui/dialog_samples.py)
    "import-csv": "import",
    "import-excel": "import_excel",
    "import-prism": "import_prism",
    "bulk-rename": "bulk_rename",
    "calculate": "calculate",
    "calculate-paired": "calculate_paired",
    "calculate-custom": "calculate_custom",
    "constants": "constants",
    "analyze-compare": "analyze_anova_dunnett",
    "analyze-fit": "analyze_fit_advanced",
    "analyze-two-way": "analyze_two_way",
    "format-graph": "format_graph",
    "format-axes": "format_axes",
    "format-series": "format_series",
    "format-colors": "format_colors",
    "export-graph": "export_graph",
    "settings": "settings",
}


def pump(app: QApplication, window: MainWindow | None = None) -> None:
    if window is not None and window._scheduler is not None:
        window.scheduler.wait_idle()
    for _ in range(10):
        app.processEvents()


def save(widget: QWidget, name: str) -> None:
    path = OUT / f"{name}.png"
    widget.grab().save(str(path))
    print(path.relative_to(OUT.parents[2]), f"{widget.width()}x{widget.height()}")


def window_shot(
    settings: Settings,
    app: QApplication,
    name: str,
    setup: Callable[[MainWindow], None],
    size: tuple[int, int] = (1100, 620),
) -> None:
    window = MainWindow(settings)
    window._ask_save = lambda _tab: SaveChoice.DISCARD  # type: ignore[method-assign]
    window.resize(*size)
    window.show()
    pump(app, window)
    setup(window)
    pump(app, window)
    save(window, name)
    window.close()
    if window._scheduler is not None:
        window._scheduler.shutdown()
    window.deleteLater()
    app.processEvents()


def launcher(window: MainWindow) -> None:
    """Nothing to do: the launcher shows the recent projects of the settings."""


def dose_response_results(window: MainWindow) -> None:
    tab = window.new_from_sample("dose_response")
    tab.show_results()


def dose_response_graph(window: MainWindow) -> None:
    tab = window.new_from_sample("dose_response")
    tab.show_graph()


def group_comparison_results(window: MainWindow) -> None:
    tab = window.new_from_sample("group_comparison")
    tab.show_results()


def categories(window: MainWindow) -> None:
    tab = window._add_tab(two_way_document(), None)
    tab.show_results()


def recent_projects(settings: Settings, root: Path) -> None:
    now = datetime.now(UTC)
    for relative, ago in (
        ("Lab/Viability/Viability 24 h.cvp", timedelta(days=3)),
        ("Lab/Cell assays/Dose response HeLa.cvp", timedelta(days=1)),
        ("Lab/Cell assays/Experiment 12.cvp", timedelta(hours=2)),
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        settings.add_recent_project(path, now - ago)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)
    assert isinstance(app, QApplication)
    ThemeManager(app).apply(Theme.LIGHT)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
        settings = Settings(store)
        ctx = Context(settings, Scheduler(debounce_ms=0))
        try:
            for name, sample in DIALOGS.items():
                dialog = SAMPLES[sample](ctx)
                dialog.show()
                settle(dialog, ctx, app)
                save(dialog, name)
                dialog.done(0)
                dialog.deleteLater()
                app.processEvents()
            recent_projects(settings, root)
            window_shot(settings, app, "launcher", launcher, (1000, 620))
            window_shot(settings, app, "main-window", dose_response_results)
            window_shot(settings, app, "graph-panel", dose_response_graph)
            window_shot(settings, app, "results-panel", group_comparison_results)
            window_shot(settings, app, "categories", categories, (1400, 620))
        finally:
            ctx.scheduler.shutdown()


if __name__ == "__main__":
    main()
