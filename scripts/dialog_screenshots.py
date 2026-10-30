"""Save an offscreen screenshot of every dialog in the light and the dark theme.

    uv run python scripts/dialog_screenshots.py docs/screenshots/after

Each dialog is shown at its default size, with the sample data of
tests/ui/dialog_samples.py. Files are named <dialog>-<theme>.png.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parents[1]))

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from curvis.compute import Scheduler  # noqa: E402
from curvis.settings import Settings, Theme  # noqa: E402
from curvis.ui.theme import ThemeManager  # noqa: E402
from tests.ui.dialog_samples import SAMPLES, Context, settle  # noqa: E402


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
        finally:
            ctx.scheduler.shutdown()


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots/after"))
