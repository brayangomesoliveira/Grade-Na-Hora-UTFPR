from __future__ import annotations

import hashlib

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QToolTip,
)

from src.core.models import ScheduleBuildResult, Turma
from src.core.schedule import DIA_LABELS_LONG


class ScheduleGridWidget(QWidget):
    """Widget de desenho da grade (rápido e independente de screenshots)."""

    def __init__(self) -> None:
        super().__init__()
        self.setMouseTracking(True)
        self._result = ScheduleBuildResult.empty()
        self._cell_info: list[tuple[QRectF, str]] = []

    def set_schedule_result(self, result: ScheduleBuildResult) -> None:
        self._result = result
        self.update()

    def _color_for_uid(self, uid: str) -> QColor:
        md5 = hashlib.md5(uid.encode("utf-8")).hexdigest()
        rgb = [int(md5[i : i + 2], 16) for i in (0, 2, 4)]
        rgb = [min(220, max(65, v)) for v in rgb]
        return QColor(rgb[0], rgb[1], rgb[2])

    def _cell_text(self, cell_turmas: list[Turma]) -> str:
        if not cell_turmas:
            return ""
        if len(cell_turmas) == 1:
            t = cell_turmas[0]
            room = next((h.room for h in t.horarios if h.room), None)
            return f"{t.disciplina_codigo} - {t.turma_codigo}" + (f"\n{room}" if room else "")
        return "\n".join(f"{t.disciplina_codigo} - {t.turma_codigo}" for t in cell_turmas[:2])

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)

        rect = self.rect()
        p.fillRect(rect, QColor("#111827"))

        margin = 8
        left_w = 80
        head_h = 40
        days = [2, 3, 4, 5, 6, 7]
        row_labels = [*(f"M{i}" for i in range(1, 7)), *(f"T{i}" for i in range(1, 7)), *(f"N{i}" for i in range(1, 6))]
        grid_w = max(10, rect.width() - 2 * margin)
        grid_h = max(10, rect.height() - 2 * margin)
        cell_w = (grid_w - left_w) / 6
        cell_h = (grid_h - head_h) / len(row_labels)

        pen_grid = QPen(QColor("#334155"))
        pen_conflict = QPen(QColor("#EF4444"))
        pen_conflict.setWidth(2)
        pen_cell = QPen(QColor("#94A3B8"))

        font_head = QFont("Segoe UI", 9, QFont.Bold)
        font_row = QFont("Segoe UI", 9, QFont.Bold)
        font_cell = QFont("Segoe UI", 7, QFont.Bold)
        p.setPen(Qt.white)

        self._cell_info = []

        # Cabeçalhos
        for i, day in enumerate(days):
            x = margin + left_w + i * cell_w
            r = QRectF(x, margin, cell_w, head_h)
            p.fillRect(r, QColor("#1D4ED8"))
            p.setPen(Qt.NoPen)
            p.drawRect(r)
            p.setPen(Qt.white)
            p.setFont(font_head)
            p.drawText(r, Qt.AlignCenter, DIA_LABELS_LONG[day])

        # Linhas + células
        for row_idx, label in enumerate(row_labels):
            y = margin + head_h + row_idx * cell_h
            rr = QRectF(margin, y, left_w, cell_h)
            p.fillRect(rr, QColor("#0F172A"))
            p.setPen(Qt.white)
            p.setFont(font_row)
            p.drawText(rr, Qt.AlignCenter, label)
            period = label[0]
            slot_num = int(label[1:])

            for day_index in range(6):
                x = margin + left_w + day_index * cell_w
                cr = QRectF(x, y, cell_w, cell_h)
                p.fillRect(cr, QColor("#EAF0F7") if row_idx % 2 == 0 else QColor("#EDF3FA"))
                p.setPen(pen_cell)
                p.drawRect(cr)
                cell_turmas = self._result.grid.get(period, {}).get(slot_num, {}).get(day_index, [])
                if not cell_turmas:
                    continue

                unique = {t.uid() for t in cell_turmas}
                conflict = len(unique) > 1
                fill = self._color_for_uid(cell_turmas[0].uid())
                inner = cr.adjusted(2, 2, -2, -2)
                p.fillRect(inner, fill)
                p.setPen(pen_conflict if conflict else QPen(fill))
                p.drawRect(inner)
                p.setPen(Qt.white)
                p.setFont(font_cell)
                txt = self._cell_text(cell_turmas)
                p.drawText(inner.adjusted(4, 2, -4, -2), Qt.AlignCenter | Qt.TextWordWrap, txt)
                tooltip_lines = [f"{t.disciplina_codigo} - {t.turma_codigo}" for t in cell_turmas]
                if conflict:
                    tooltip_lines.append("")
                    tooltip_lines.append("CONFLITO DETECTADO")
                self._cell_info.append((inner, "\n".join(tooltip_lines)))

        p.setPen(pen_grid)
        p.drawRect(QRectF(margin, margin, grid_w, grid_h))
        p.end()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        for rect, text in self._cell_info:
            if rect.contains(pos):
                QToolTip.showText(self.mapToGlobal(QPoint(int(pos.x()), int(pos.y()))), text, self)
                return
        QToolTip.hideText()
        super().mouseMoveEvent(event)


class GradePanel(QFrame):
    """Painel direito: grade visual + exportação PNG."""

    export_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Card")
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        top_line = QFrame()
        top_line.setObjectName("CardTitleBar")
        root.addWidget(top_line)

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        root.addWidget(body)

        row = QHBoxLayout()
        title = QLabel("Grade Semanal")
        title.setObjectName("TitleLabel")
        row.addWidget(title)
        row.addStretch(1)
        self.conflict_label = QLabel("Conflitos: 0")
        self.conflict_label.setObjectName("MutedLabel")
        row.addWidget(self.conflict_label)
        self.btn_export = QPushButton("EXPORTAR IMAGEM (PNG)")
        self.btn_export.setObjectName("PrimaryButton")
        self.btn_export.clicked.connect(self.export_requested.emit)
        row.addWidget(self.btn_export)
        lay.addLayout(row)

        self.grid_widget = ScheduleGridWidget()
        self.grid_widget.setMinimumHeight(540)
        lay.addWidget(self.grid_widget, 1)

        self.status_label = QLabel("Pronto.")
        self.status_label.setObjectName("MutedLabel")
        lay.addWidget(self.status_label)

    def set_busy(self, busy: bool) -> None:
        self.btn_export.setEnabled(not busy)

    def set_status(self, text: str, *, error: bool = False) -> None:
        self.status_label.setText(text)
        if error:
            self.status_label.setStyleSheet("color: #FCA5A5;")
        else:
            self.status_label.setStyleSheet("")

    def set_schedule_result(self, result: ScheduleBuildResult) -> None:
        self.grid_widget.set_schedule_result(result)
        self.conflict_label.setText(f"Conflitos: {len(result.conflitos)}")
        if result.conflitos:
            self.conflict_label.setStyleSheet("color: #FCA5A5; font-weight: 700;")
        else:
            self.conflict_label.setStyleSheet("")
