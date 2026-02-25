from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class LoginPanel(QFrame):
    """Painel de login e utilidades de entrada (cache/logs)."""

    login_requested = Signal(dict)
    load_cache_requested = Signal()
    cancel_requested = Signal()
    continue_manual_requested = Signal()
    open_logs_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Card")
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        title_line = QFrame()
        title_line.setObjectName("CardTitleBar")
        root.addWidget(title_line)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 12, 16, 16)
        body_layout.setSpacing(8)
        root.addWidget(body)

        title = QLabel("Login no Portal UTFPR")
        title.setObjectName("TitleLabel")
        body_layout.addWidget(title)

        info = QLabel("Senha fica apenas em memória. Use cache JSON se quiser testar sem scraping.")
        info.setObjectName("MutedLabel")
        info.setWordWrap(True)
        body_layout.addWidget(info)

        self.ra_input = QLineEdit()
        self.ra_input.setPlaceholderText("RA (matrícula)")
        body_layout.addWidget(self.ra_input)

        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setPlaceholderText("Senha")
        body_layout.addWidget(self.password_input)

        self.prefix_check = QCheckBox("Prefixo 'a' no usuário")
        self.prefix_check.setChecked(True)
        body_layout.addWidget(self.prefix_check)

        self.debug_check = QCheckBox("Modo debug (browser visível)")
        self.debug_check.setChecked(False)
        body_layout.addWidget(self.debug_check)

        row1 = QHBoxLayout()
        body_layout.addLayout(row1)
        self.btn_login = QPushButton("ENTRAR")
        self.btn_login.setObjectName("PrimaryButton")
        self.btn_login.clicked.connect(self._emit_login)
        row1.addWidget(self.btn_login)
        self.btn_cancel = QPushButton("CANCELAR")
        self.btn_cancel.setObjectName("DangerButton")
        self.btn_cancel.clicked.connect(self.cancel_requested.emit)
        self.btn_cancel.setEnabled(False)
        row1.addWidget(self.btn_cancel)

        row2 = QHBoxLayout()
        body_layout.addLayout(row2)
        self.btn_cache = QPushButton("Carregar cache JSON")
        self.btn_cache.clicked.connect(self.load_cache_requested.emit)
        row2.addWidget(self.btn_cache)
        self.btn_logs = QPushButton("Abrir pasta logs")
        self.btn_logs.clicked.connect(self.open_logs_requested.emit)
        row2.addWidget(self.btn_logs)

        self.btn_continue = QPushButton("Continuar (captcha/2FA)")
        self.btn_continue.setObjectName("PrimaryButton")
        self.btn_continue.clicked.connect(self.continue_manual_requested.emit)
        self.btn_continue.hide()
        body_layout.addWidget(self.btn_continue)

        self.status_label = QLabel("Aguardando login.")
        self.status_label.setObjectName("MutedLabel")
        self.status_label.setWordWrap(True)
        body_layout.addWidget(self.status_label)

        body_layout.addStretch(1)

    def _emit_login(self) -> None:
        self.login_requested.emit(self.get_form_data())

    def get_form_data(self) -> dict[str, object]:
        return {
            "ra": self.ra_input.text().strip(),
            "password": self.password_input.text(),
            "add_prefix_a": self.prefix_check.isChecked(),
            "debug_browser": self.debug_check.isChecked(),
        }

    def set_defaults(self, *, add_prefix_a: bool, debug_browser: bool) -> None:
        self.prefix_check.setChecked(add_prefix_a)
        self.debug_check.setChecked(debug_browser)

    def set_busy(self, busy: bool) -> None:
        self.btn_login.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)
        self.ra_input.setEnabled(not busy)
        self.password_input.setEnabled(not busy)
        self.prefix_check.setEnabled(not busy)
        self.debug_check.setEnabled(not busy)

    def show_manual_continue(self, visible: bool) -> None:
        self.btn_continue.setVisible(visible)

    def set_status(self, text: str, *, error: bool = False) -> None:
        self.status_label.setText(text)
        if error:
            self.status_label.setStyleSheet("color: #FCA5A5;")
        else:
            self.status_label.setStyleSheet("")
