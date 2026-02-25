from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


CAMPUS_OPTIONS: tuple[str, ...] = (
    "Curitiba",
    "Apucarana",
    "Campo Mourão",
    "Cornélio Procópio",
    "Dois Vizinhos",
    "Francisco Beltrão",
    "Guarapuava",
    "Londrina",
    "Medianeira",
    "Pato Branco",
    "Ponta Grossa",
    "Santa Helena",
    "Toledo",
)


class LoginPanel(QFrame):
    """Painel de login com selecao de cidade/campus."""

    login_requested = Signal(dict)
    cancel_requested = Signal()
    continue_manual_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Card")
        self.setMinimumWidth(540)
        self.setMinimumHeight(430)
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
        body_layout.setContentsMargins(20, 16, 20, 20)
        body_layout.setSpacing(10)
        root.addWidget(body)

        title = QLabel("Login no Portal UTFPR")
        title.setObjectName("TitleLabel")
        body_layout.addWidget(title)

        self.campus_combo = QComboBox()
        self.campus_combo.addItems(list(CAMPUS_OPTIONS))
        self.campus_combo.setCurrentText("Curitiba")
        self.campus_combo.setToolTip("Cidade / Campus")
        self.campus_combo.setMinimumHeight(34)
        body_layout.addWidget(self.campus_combo)

        self.ra_input = QLineEdit()
        self.ra_input.setPlaceholderText("RA (matricula)")
        self.ra_input.setMinimumHeight(36)
        body_layout.addWidget(self.ra_input)

        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setPlaceholderText("Senha")
        self.password_input.setMinimumHeight(36)
        body_layout.addWidget(self.password_input)

        self.prefix_check = QCheckBox("Prefixo 'a' no usuario")
        self.prefix_check.setChecked(True)
        body_layout.addWidget(self.prefix_check)

        self.debug_check = QCheckBox("Modo debug (browser visivel)")
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
            "campus_name": self.campus_combo.currentText().strip(),
            "add_prefix_a": self.prefix_check.isChecked(),
            "debug_browser": self.debug_check.isChecked(),
        }

    def set_defaults(self, *, add_prefix_a: bool, debug_browser: bool, campus_name: str = "Curitiba") -> None:
        self.prefix_check.setChecked(add_prefix_a)
        self.debug_check.setChecked(debug_browser)
        idx = self.campus_combo.findText(campus_name)
        if idx >= 0:
            self.campus_combo.setCurrentIndex(idx)

    def set_busy(self, busy: bool) -> None:
        self.btn_login.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)
        self.campus_combo.setEnabled(not busy)
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
