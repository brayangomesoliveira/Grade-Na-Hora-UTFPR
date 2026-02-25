from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.models import ScheduleBuildResult, Turma
from src.core.schedule import conflict_uids


class TurmasPanel(QFrame):
    """Painel esquerdo: filtro, lista com checkbox e ações."""

    generate_requested = Signal()
    clear_requested = Signal()
    report_requested = Signal()
    refresh_requested = Signal()
    back_requested = Signal()
    cancel_requested = Signal()
    open_logs_requested = Signal()
    selection_changed = Signal()
    credit_limit_changed = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Card")
        self._turmas: list[Turma] = []
        self._selected_ids: set[str] = set()
        self._conflict_ids: set[str] = set()
        self._updating_tree = False
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

        title = QLabel("Turmas Abertas")
        title.setObjectName("TitleLabel")
        lay.addWidget(title)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Buscar por código / nome / professor")
        self.search_input.textChanged.connect(self._refresh_tree)
        lay.addWidget(self.search_input)

        info_row = QHBoxLayout()
        self.creditos_label = QLabel("Créditos usados: 0")
        info_row.addWidget(self.creditos_label)
        info_row.addSpacing(8)
        info_row.addWidget(QLabel("Limite:"))
        self.credit_limit = QSpinBox()
        self.credit_limit.setRange(0, 999)
        self.credit_limit.setValue(40)
        self.credit_limit.valueChanged.connect(self.credit_limit_changed.emit)
        info_row.addWidget(self.credit_limit)
        self.limit_alert = QLabel("")
        self.limit_alert.setObjectName("MutedLabel")
        info_row.addWidget(self.limit_alert, 1)
        lay.addLayout(info_row)

        self.tree = QTreeWidget()
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setColumnCount(10)
        self.tree.setHeaderLabels(
            ["Sel", "Código", "Disciplina", "Turma", "Horários", "Professor", "Vagas", "Cal", "Status", "Conf"]
        )
        widths = [44, 90, 220, 60, 190, 180, 60, 45, 75, 55]
        for i, w in enumerate(widths):
            self.tree.setColumnWidth(i, w)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemDoubleClicked.connect(self._toggle_item_double_click)
        lay.addWidget(self.tree, 1)

        row1 = QHBoxLayout()
        self.btn_generate = QPushButton("GERAR")
        self.btn_generate.setObjectName("PrimaryButton")
        self.btn_generate.clicked.connect(self.generate_requested.emit)
        row1.addWidget(self.btn_generate)

        self.btn_clear = QPushButton("LIMPAR")
        self.btn_clear.clicked.connect(self.clear_requested.emit)
        row1.addWidget(self.btn_clear)

        self.btn_report = QPushButton("RELATÓRIO")
        self.btn_report.clicked.connect(self.report_requested.emit)
        row1.addWidget(self.btn_report)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        self.btn_refresh = QPushButton("ATUALIZAR")
        self.btn_refresh.clicked.connect(self.refresh_requested.emit)
        row2.addWidget(self.btn_refresh)
        self.btn_cancel = QPushButton("CANCELAR")
        self.btn_cancel.setObjectName("DangerButton")
        self.btn_cancel.clicked.connect(self.cancel_requested.emit)
        self.btn_cancel.setEnabled(False)
        row2.addWidget(self.btn_cancel)
        self.btn_back = QPushButton("VOLTAR")
        self.btn_back.clicked.connect(self.back_requested.emit)
        row2.addWidget(self.btn_back)
        lay.addLayout(row2)

        row3 = QHBoxLayout()
        self.btn_open_logs = QPushButton("Abrir logs")
        self.btn_open_logs.clicked.connect(self.open_logs_requested.emit)
        row3.addWidget(self.btn_open_logs)
        row3.addStretch(1)
        lay.addLayout(row3)

        self.status_label = QLabel("Pronto.")
        self.status_label.setObjectName("MutedLabel")
        self.status_label.setWordWrap(True)
        lay.addWidget(self.status_label)

    # ---------- API ----------
    def set_busy(self, busy: bool) -> None:
        self.btn_cancel.setEnabled(busy)
        self.btn_refresh.setEnabled(not busy)
        self.btn_generate.setEnabled(not busy)

    def set_status(self, text: str, *, error: bool = False) -> None:
        self.status_label.setText(text)
        if error:
            self.status_label.setStyleSheet("color: #FCA5A5;")
        else:
            self.status_label.setStyleSheet("")

    def set_credit_limit(self, value: int) -> None:
        self.credit_limit.setValue(max(0, int(value)))

    def get_credit_limit(self) -> int:
        return int(self.credit_limit.value())

    def get_selected_ids(self) -> set[str]:
        return set(self._selected_ids)

    def set_selected_ids(self, ids: Iterable[str]) -> None:
        self._selected_ids = set(ids)
        self._refresh_tree()

    def clear_selection(self) -> None:
        self._selected_ids.clear()
        self._refresh_tree()
        self.selection_changed.emit()

    def set_turmas(self, turmas: list[Turma], *, selected_ids: set[str] | None = None) -> None:
        self._turmas = list(turmas)
        if selected_ids is not None:
            self._selected_ids = set(selected_ids)
        self._refresh_tree()

    def update_schedule_info(self, result: ScheduleBuildResult) -> None:
        self.creditos_label.setText(f"Créditos usados: {result.creditos_usados}")
        new_conflict_ids = conflict_uids(result)
        conflicts_changed = new_conflict_ids != self._conflict_ids
        self._conflict_ids = new_conflict_ids
        limite = self.get_credit_limit()
        if result.creditos_usados > limite:
            self.limit_alert.setText(f"Limite excedido ({result.creditos_usados}/{limite})")
            self.limit_alert.setStyleSheet("color: #FBBF24;")
        else:
            self.limit_alert.setText("")
            self.limit_alert.setStyleSheet("")
        if conflicts_changed:
            self._refresh_tree()

    # ---------- Tree internals ----------
    def _sort_key(self, turma: Turma) -> tuple[int, int, str, str]:
        uid = turma.uid()
        is_conflict_first = 0 if uid in self._conflict_ids else 1
        is_selected_first = 0 if uid in self._selected_ids else 1
        return (is_conflict_first, is_selected_first, turma.disciplina_codigo, turma.turma_codigo)

    def _refresh_tree(self) -> None:
        query = self.search_input.text().strip().lower()
        self._updating_tree = True
        self.tree.setUpdatesEnabled(False)
        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            for turma in sorted(self._turmas, key=self._sort_key):
                blob = f"{turma.disciplina_codigo} {turma.disciplina_nome} {turma.professor or ''}".lower()
                if query and query not in blob:
                    continue
                uid = turma.uid()
                horarios_txt = turma.horarios_compactos()
                prof_txt = turma.professor or "-"
                vagas_txt = "-" if turma.vagas_total is None else str(turma.vagas_total)
                vagas_cal_txt = "-" if turma.vagas_calouros is None else str(turma.vagas_calouros)
                status_txt = turma.status or "-"
                conflito_sel = uid in self._conflict_ids and uid in self._selected_ids
                item = QTreeWidgetItem(
                    [
                        "",
                        turma.disciplina_codigo,
                        turma.disciplina_nome,
                        turma.turma_codigo,
                        horarios_txt,
                        prof_txt,
                        vagas_txt,
                        vagas_cal_txt,
                        status_txt,
                        "Sim" if conflito_sel else "N?o",
                    ]
                )
                item.setData(0, Qt.UserRole, uid)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                item.setCheckState(0, Qt.Checked if uid in self._selected_ids else Qt.Unchecked)
                if conflito_sel:
                    for col in range(item.columnCount()):
                        item.setToolTip(col, "Turma em conflito de horário")
                item.setToolTip(
                    2,
                    (
                        f"{turma.disciplina_codigo} - {turma.disciplina_nome} - {turma.turma_codigo} - "
                        f"{horarios_txt} - Prof: {prof_txt} - Vagas: {vagas_txt} - Status: {status_txt}"
                    ),
                )
                self.tree.addTopLevelItem(item)
        finally:
            self.tree.blockSignals(False)
            self.tree.setUpdatesEnabled(True)
            self._updating_tree = False

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating_tree or column != 0:
            return
        uid = item.data(0, Qt.UserRole)
        if not uid:
            return
        if item.checkState(0) == Qt.Checked:
            self._selected_ids.add(str(uid))
        else:
            self._selected_ids.discard(str(uid))
        self.selection_changed.emit()
        self._refresh_tree()

    def _toggle_item_double_click(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._updating_tree:
            return
        item.setCheckState(0, Qt.Unchecked if item.checkState(0) == Qt.Checked else Qt.Checked)
