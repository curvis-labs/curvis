"""Application entry point."""

import sys

from PySide6.QtWidgets import QApplication

from curvis.ui.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    """Start the Curvis application and return its exit code."""
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("Curvis")
    window = MainWindow()
    window.show()
    return app.exec()
