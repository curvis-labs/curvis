"""The Settings dialog. Changes apply when you click OK."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from curvis.settings import Settings, Theme
from curvis.settings.store import DECIMAL_PLACES_MAX, DECIMAL_PLACES_MIN


class SettingsDialog(QDialog):
    """Theme and decimal places. More settings arrive in step 3.5."""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self._settings = settings

        self.theme = QComboBox(self)
        for theme in Theme:
            self.theme.addItem(theme.label, theme)
        self.theme.setCurrentIndex(self.theme.findData(settings.theme))

        self.decimal_places = QSpinBox(self)
        self.decimal_places.setRange(DECIMAL_PLACES_MIN, DECIMAL_PLACES_MAX)
        self.decimal_places.setValue(settings.decimal_places)
        self.decimal_places.setToolTip("Number of decimal places shown in calculated columns.")

        form = QFormLayout()
        form.addRow("&Theme", self.theme)
        form.addRow("&Decimal places in calculated columns", self.decimal_places)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.buttons)

    def accept(self) -> None:
        theme = Theme(self.theme.currentData())
        if theme != self._settings.theme:
            self._settings.theme = theme
        if self.decimal_places.value() != self._settings.decimal_places:
            self._settings.decimal_places = self.decimal_places.value()
        super().accept()
