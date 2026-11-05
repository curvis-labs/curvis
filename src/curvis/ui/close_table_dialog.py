"""The warning shown before a table is closed (docs/DESIGN.md section 8).

Closing a table deletes it from the project, with its analyses and graphs. The dialog
says so, lists what goes with it and that Undo brings it back. Its buttons are
*Delete table* and *Cancel*; Cancel has the default focus, so Enter does not delete
by accident.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QStyle, QVBoxLayout, QWidget

from curvis.analysis.base import Analysis
from curvis.charts.spec import ChartSpec
from curvis.ui.dialogs import SPACING, DialogSkeleton, secondary_label, size_simple

WARNING_ICON_SIZE = 32
LEAD_MAX_WIDTH = 460
"""The first line stays on one line up to this width; a longer table name wraps."""


def _listed(n: int, one: str, many: str, names: Sequence[str]) -> str:
    counts = Counter(names)
    kinds = ", ".join(name if k == 1 else f"{k} × {name}" for name, k in counts.items())
    return f"{n} {one if n == 1 else many} ({kinds})"


def _analysis_name(analysis: Analysis) -> str:
    try:
        return analysis.analysis_type.name
    except KeyError:
        return analysis.type


def _graph_name(graph: ChartSpec) -> str:
    return graph.family.label


def belongings(analyses: Sequence[Analysis], graphs: Sequence[ChartSpec]) -> list[str]:
    """Lines such as "1 analysis (Unpaired t test)" and "2 graphs (XY, Bar)"."""
    lines = []
    if analyses:
        names = [_analysis_name(a) for a in analyses]
        lines.append(_listed(len(analyses), "analysis", "analyses", names))
    if graphs:
        lines.append(_listed(len(graphs), "graph", "graphs", [_graph_name(g) for g in graphs]))
    return lines


class CloseTableDialog(QDialog):
    """Asks whether to delete the table ``name`` with its ``analyses`` and ``graphs``."""

    def __init__(
        self,
        name: str,
        analyses: Sequence[Analysis] = (),
        graphs: Sequence[ChartSpec] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Close table")
        self.skeleton = DialogSkeleton(self, "Delete table")
        row = QHBoxLayout()
        row.setSpacing(2 * SPACING)
        icon = QLabel(self)
        icon.setObjectName("warningIcon")
        pixmap = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
        icon.setPixmap(pixmap.pixmap(WARNING_ICON_SIZE, WARNING_ICON_SIZE))
        icon.setAccessibleName("Warning")
        row.addWidget(icon, 0)
        text = QVBoxLayout()
        text.setSpacing(SPACING)
        self.lead = QLabel(f"Closing “{name}” deletes the table from the project.", self)
        self.lead.setTextFormat(Qt.TextFormat.PlainText)
        self.lead.setWordWrap(True)
        width = self.lead.fontMetrics().horizontalAdvance(self.lead.text()) + 2
        self.lead.setMinimumWidth(min(width, LEAD_MAX_WIDTH))
        text.addWidget(self.lead)
        self.items = belongings(analyses, graphs)
        if self.items:
            listed = "".join(f"<li>{line}</li>" for line in self.items)
            self.also = QLabel(
                f"This also deletes what belongs to it:<ul style='margin: 0'>{listed}</ul>", self
            )
            self.also.setWordWrap(True)
            text.addWidget(self.also)
        self.undo_hint = secondary_label("You can bring it back with Undo (Ctrl+Z).", self)
        self.undo_hint.setWordWrap(True)
        text.addWidget(self.undo_hint)
        text.addStretch(1)
        row.addLayout(text, 1)
        self.skeleton.body.addLayout(row)
        ok = self.skeleton.ok_button
        assert ok is not None
        ok.setAutoDefault(False)
        ok.setDefault(False)
        self.cancel_button = self.skeleton.buttons.button(
            self.skeleton.buttons.StandardButton.Cancel
        )
        self.cancel_button.setDefault(True)
        self.cancel_button.setFocus()
        size_simple(self)

    @property
    def delete_button(self) -> QWidget:
        ok = self.skeleton.ok_button
        assert ok is not None
        return ok
