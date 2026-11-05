"""The Export graph dialog: the file format and, for PNG, the resolution.

A simple dialog (docs/DESIGN.md §1). The graph is exported at the size of its page
(Format graph, Graph tab); the dialog says what size that is, and for PNG how many
pixels it makes. Clicking *Export…* asks where to save the file.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QSpinBox, QWidget

from curvis.charts.render_mpl.export import DEFAULT_DPI, MAX_DPI, MIN_DPI, ExportFormat
from curvis.charts.spec import ChartSpec
from curvis.ui.dialogs import (
    FIELD_WIDTH,
    DialogSkeleton,
    buddy_label,
    form_layout,
    secondary_label,
    size_simple,
)

FORMAT_HINTS = {
    ExportFormat.PNG: "An image for slides and documents.",
    ExportFormat.SVG: "A drawing you can edit; its text needs the DejaVu Sans font.",
    ExportFormat.PDF: "A drawing for print and journals, with its font embedded.",
}


class ExportDialog(QDialog):
    """Choose how to export ``spec``."""

    def __init__(
        self,
        spec: ChartSpec,
        *,
        fmt: ExportFormat = ExportFormat.PNG,
        dpi: int = DEFAULT_DPI,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.spec = spec
        self.setWindowTitle("Export graph")
        self.skeleton = DialogSkeleton(self, "Export…")
        form = form_layout()
        self.format_combo = QComboBox()
        for choice in ExportFormat:
            self.format_combo.addItem(f"{choice.value.upper()} · {choice.label}", choice)
        form.addRow(buddy_label("&Format", self.format_combo), self.format_combo)
        self.dpi = QSpinBox()
        self.dpi.setRange(MIN_DPI, MAX_DPI)
        self.dpi.setSingleStep(50)
        self.dpi.setSuffix(" dpi")
        self.dpi.setMaximumWidth(FIELD_WIDTH)
        self.dpi.setValue(dpi)
        form.addRow(buddy_label("&Resolution", self.dpi), self.dpi)
        self.skeleton.body.addLayout(form)
        self.hint = secondary_label()
        self.size_note = secondary_label()
        self.skeleton.body.addWidget(self.hint)
        self.skeleton.body.addWidget(self.size_note)
        self.skeleton.body.addStretch(1)
        self.format_combo.currentIndexChanged.connect(lambda _i: self._update())
        self.dpi.valueChanged.connect(lambda _v: self._update())
        self.format_combo.setCurrentIndex(self.format_combo.findData(fmt))
        self._update()
        size_simple(self)
        # The hints wrap: keep the height they need at the narrowest width.
        self.setMinimumHeight(self.sizeHint().height())
        self.format_combo.setFocus()

    @property
    def export_format(self) -> ExportFormat:
        return ExportFormat(self.format_combo.currentData())

    def _update(self) -> None:
        fmt = self.export_format
        png = fmt is ExportFormat.PNG
        self.dpi.setEnabled(png)
        self.hint.setText(FORMAT_HINTS[fmt])
        width, height = self.spec.page_size_cm
        size = f"{width:g} × {height:g} cm"
        if png:
            from curvis.charts.axes import page_px

            pixels = page_px(self.spec, self.dpi.value())
            size += f", {pixels[0]:,} × {pixels[1]:,} pixels"
        self.size_note.setText(f"The graph is exported at its size: {size}.")


__all__ = ("ExportDialog",)
