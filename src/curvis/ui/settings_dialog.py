"""The Settings dialog. Changes apply when you click Save."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QSpinBox, QWidget

from curvis.settings import Settings, Theme
from curvis.settings.store import DECIMAL_PLACES_MAX, DECIMAL_PLACES_MIN
from curvis.ui.dialogs import DialogSkeleton, form_layout, size_simple


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

        self.skeleton = DialogSkeleton(self, "Save")
        self.buttons = self.skeleton.buttons
        form = form_layout()
        form.addRow("&Theme", self.theme)
        form.addRow("&Decimal places in calculated columns", self.decimal_places)
        self.skeleton.body.addLayout(form)
        self.skeleton.body.addStretch(1)
        size_simple(self)

    def accept(self) -> None:
        theme = Theme(self.theme.currentData())
        if theme != self._settings.theme:
            self._settings.theme = theme
        if self.decimal_places.value() != self._settings.decimal_places:
            self._settings.decimal_places = self.decimal_places.value()
        super().accept()
