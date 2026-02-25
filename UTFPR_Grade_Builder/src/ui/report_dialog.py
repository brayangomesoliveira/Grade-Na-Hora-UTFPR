from __future__ import annotations

from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QTextEdit, QVBoxLayout


class ReportDialog(QDialog):
    """Diálogo simples para mostrar relatório da seleção."""

    def __init__(self, parent, *, text: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Relatório")
        self.resize(820, 560)
        self._build_ui(text)

    def _build_ui(self, text: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        box = QTextEdit()
        box.setReadOnly(True)
        box.setPlainText(text)
        layout.addWidget(box, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        btn_close = QPushButton("Fechar")
        btn_close.clicked.connect(self.accept)
        row.addWidget(btn_close)
        layout.addLayout(row)
