"""The main application window."""

from PySide6.QtWidgets import QMainWindow, QWidget


class MainWindow(QMainWindow):
    """Top-level window that will hold one tab per open document."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Curvis")
        self.resize(1200, 800)
