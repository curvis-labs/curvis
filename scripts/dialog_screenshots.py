"""Save an offscreen screenshot of every dialog, and of the main window with a table,
in the light and the dark theme.

    uv run python scripts/dialog_screenshots.py docs/screenshots/after

Each dialog is shown at its default size, with the sample data of
tests/ui/dialog_samples.py. Files are named <dialog>-<theme>.png; the main window is
main_window-<theme>.png.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parents[1]))

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from tests.ui.dialog_samples import SAMPLES, Context, settle

from curvis.compute import Scheduler
from curvis.settings import Settings, Theme
from curvis.ui.main_window import MainWindow, SaveChoice
from curvis.ui.theme import ThemeManager

SAMPLE_ROWS = [
    ["0.01", "10.2", "11.4"],
    ["0.1", "9.9", "18.4"],
    ["1", "10.1", "52.3"],
    ["10", "10.8", "82.6"],
]


def main_window(settings: Settings, app: QApplication, path: Path) -> None:
    """Two document tabs; the second has a few rows of data."""
    window = MainWindow(settings)
    window._ask_save = lambda _tab: SaveChoice.DISCARD  # type: ignore[method-assign]
    window.resize(900, 420)
    window.show()
    window.new_document()
    tab = window.new_document()
    assert tab.table_view is not None
    tab.table_view.model().write_block(0, 0, SAMPLE_ROWS)
    for _ in range(5):
        app.processEvents()
    window.grab().save(str(path))
    print(path, f"{window.width()}x{window.height()}")
    window.close()
    if window._scheduler is not None:
        window._scheduler.shutdown()
    window.deleteLater()
    app.processEvents()


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)
    assert isinstance(app, QApplication)
    themes = ThemeManager(app)
    with tempfile.TemporaryDirectory() as tmp:
        store = QSettings(str(Path(tmp) / "settings.ini"), QSettings.Format.IniFormat)
        ctx = Context(Settings(store), Scheduler(debounce_ms=0))
        try:
            for theme in (Theme.LIGHT, Theme.DARK):
                themes.apply(theme)
                for name, make in SAMPLES.items():
                    dialog = make(ctx)
                    dialog.show()
                    settle(dialog, ctx, app)
                    path = out / f"{name}-{theme.value}.png"
                    dialog.grab().save(str(path))
                    print(path, f"{dialog.width()}x{dialog.height()}")
                    dialog.done(0)
                    dialog.deleteLater()
                    app.processEvents()
                main_window(ctx.settings, app, out / f"main_window-{theme.value}.png")
        finally:
            ctx.scheduler.shutdown()


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots/after"))
