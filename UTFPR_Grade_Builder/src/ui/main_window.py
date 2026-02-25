from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.core.export_png import default_png_name, export_schedule_png
from src.core.models import ScheduleBuildResult, Turma
from src.core.schedule import build_schedule, selected_turmas, summarize_selection
from src.core.state import AppState, AppStatus, LoginRequest, ProgressInfo
from src.core.storage import DEFAULT_STORAGE_STATE_PATH, load_app_state, load_turmas_cache, save_app_state
from src.infra.cancel_token import CancelToken, CancelledError
from src.infra.logger import open_logs_folder
from src.infra.scraper_async import (
    CourseSelectionRequired,
    LoginResult,
    ScraperError,
    SelectorChangedError,
    UtfprScraperAsync,
)
from src.ui.grade_panel import GradePanel
from src.ui.login_panel import LoginPanel
from src.ui.report_dialog import ReportDialog
from src.ui.styles import status_badge_style
from src.ui.turmas_panel import TurmasPanel

logger = logging.getLogger(__name__)


class ScrapeWorker(QObject):
    """Worker em QThread: executa Playwright (asyncio) fora da UI thread."""

    progress = Signal(object)  # ProgressInfo
    manual_step_required = Signal(str)
    course_selection_required = Signal(object, str)  # payload(dict), message
    turmas_ready = Signal(object, str)  # list[Turma], source
    error = Signal(str)
    task_finished = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cancel_token: CancelToken | None = None
        self._scraper: UtfprScraperAsync | None = None
        self._manual_continue = threading.Event()
        self._course_continue = threading.Event()
        self._course_payload: dict[str, Any] | None = None
        self._lock = threading.Lock()

    # ---------- API chamada da UI ----------
    @Slot(dict)
    def run_login_and_scrape(self, payload: dict[str, Any]) -> None:
        self._run_task("login_and_scrape", lambda token: self._coro_login_and_scrape(payload, token))

    @Slot(dict)
    def run_refresh_with_session(self, payload: dict[str, Any]) -> None:
        self._run_task(
            "refresh_with_session",
            lambda token: self._coro_refresh_with_session(payload, token),
        )

    @Slot()
    def continue_after_manual_step(self) -> None:
        self._manual_continue.set()

    @Slot(dict)
    def submit_course_selection(self, payload: dict[str, Any]) -> None:
        self._course_payload = dict(payload)
        self._course_continue.set()

    @Slot()
    def cancel(self) -> None:
        with self._lock:
            token = self._cancel_token
            scraper = self._scraper
        if token is not None:
            token.cancel()
        self._manual_continue.set()
        self._course_continue.set()
        if scraper is not None:
            scraper.request_force_close_threadsafe()

    # ---------- Motor de task ----------
    def _emit_progress(self, status: AppStatus, message: str, *, detail: str | None = None) -> None:
        self.progress.emit(ProgressInfo(status=status, message=message, detail=detail))

    def _run_task(self, task_name: str, coro_factory) -> None:
        self._manual_continue.clear()
        self._course_continue.clear()
        self._course_payload = None
        token = CancelToken()
        with self._lock:
            self._cancel_token = token
            self._scraper = None

        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(coro_factory(token))
        except CancelledError:
            self._emit_progress(AppStatus.CANCELED, "Operacao cancelada.")
        except SelectorChangedError as exc:
            self.error.emit(
                f"{exc}\n\nModo erro assistido: ajuste os seletores em src/infra/selectors.py "
                "usando logs/screenshots e logs/html."
            )
        except (ScraperError, RuntimeError) as exc:
            self.error.emit(str(exc))
        except Exception as exc:  # pragma: no cover - proteção extra
            logger.exception("Falha inesperada no worker")
            self.error.emit(f"Erro inesperado no worker: {exc}")
        finally:
            with contextlib.suppress(Exception):
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            with contextlib.suppress(Exception):
                loop.stop()
                loop.close()
            self._loop = None
            with self._lock:
                self._cancel_token = None
                self._scraper = None
            self.task_finished.emit(task_name)

    # ---------- Coroutines ----------
    async def _wait_manual_continue(self, token: CancelToken) -> None:
        while not self._manual_continue.is_set():
            token.raise_if_cancelled()
            await asyncio.sleep(0.15)

    async def _wait_course_selection(self, token: CancelToken) -> dict[str, Any]:
        while not self._course_continue.is_set():
            token.raise_if_cancelled()
            await asyncio.sleep(0.15)
        token.raise_if_cancelled()
        payload = dict(self._course_payload or {})
        self._course_payload = None
        self._course_continue.clear()
        return payload

    @staticmethod
    def _serialize_course_options(options: list[object]) -> list[dict[str, object]]:
        serialized: list[dict[str, object]] = []
        for opt in options:
            label = str(getattr(opt, "label", "")).strip()
            if not label or bool(getattr(opt, "placeholder", False)):
                continue
            serialized.append(
                {
                    "value": str(getattr(opt, "value", "")).strip(),
                    "label": label,
                    "selected": bool(getattr(opt, "selected", False)),
                }
            )
        return serialized

    async def _open_turmas_with_course_selection(
        self,
        scraper: UtfprScraperAsync,
        *,
        token: CancelToken,
        preferred_value: str | None = None,
        preferred_label: str | None = None,
    ) -> tuple[str | None, str | None]:
        remembered_value = preferred_value
        remembered_label = preferred_label
        navigate_first = True

        while True:
            token.raise_if_cancelled()
            try:
                if navigate_first:
                    await scraper.go_to_turmas_abertas(token=token)
                    navigate_first = False
                else:
                    await scraper.ensure_turmas_table_ready(token=token)
                return (remembered_value, remembered_label)
            except CourseSelectionRequired as exc:
                options_payload = self._serialize_course_options(exc.options)
                auto_choice = scraper.choose_portal_course_option(
                    exc.options,
                    preferred_value=remembered_value,
                    preferred_label=remembered_label,
                )
                self.course_selection_required.emit(
                    {
                        "options": options_payload,
                        "selected_value": auto_choice.value if auto_choice else None,
                    },
                    str(exc),
                )
                self._emit_progress(AppStatus.SCRAPING, str(exc))

                user_payload = await self._wait_course_selection(token)
                chosen_value = str(user_payload.get("portal_course_value", "")).strip() or None
                chosen_label = str(user_payload.get("portal_course_label", "")).strip() or None
                if not chosen_value and not chosen_label:
                    raise ScraperError("Selecao de curso vazia.")

                self._emit_progress(AppStatus.SCRAPING, "Selecionando curso em Turmas Abertas...")
                try:
                    selected_label = await scraper.select_portal_course(
                        course_value=chosen_value,
                        course_label=chosen_label,
                        token=token,
                    )
                except CourseSelectionRequired:
                    navigate_first = False
                    remembered_value = chosen_value or remembered_value
                    remembered_label = chosen_label or remembered_label
                    continue

                remembered_value = chosen_value or remembered_value
                remembered_label = selected_label or chosen_label or remembered_label
                return (remembered_value, remembered_label)

    def _make_scraper(
        self,
        *,
        debug_browser: bool,
        token: CancelToken,
        campus_name: str | None = None,
    ) -> UtfprScraperAsync:
        timeout_ms = int(os.getenv("UTFPR_TIMEOUT_MS", "80000"))
        retries = int(os.getenv("UTFPR_SCRAPER_RETRIES", "2"))
        scraper = UtfprScraperAsync(
            headless=not debug_browser,
            timeout_ms=timeout_ms,
            storage_state_path=DEFAULT_STORAGE_STATE_PATH,
            retries=retries,
            default_campus_name=campus_name,
        )
        loop = asyncio.get_running_loop()
        scraper.bind_runtime(loop=loop, cancel_token=token)
        with self._lock:
            self._scraper = scraper
        return scraper

    async def _coro_login_and_scrape(self, payload: dict[str, Any], token: CancelToken) -> None:
        req = LoginRequest(
            ra=str(payload.get("ra", "")),
            password=str(payload.get("password", "")),
            campus_name=str(payload.get("campus_name", "Curitiba")),
            portal_course_value=str(payload.get("portal_course_value", "")).strip() or None,
            portal_course_label=str(payload.get("portal_course_label", "")).strip() or None,
            add_prefix_a=bool(payload.get("add_prefix_a", True)),
            debug_browser=bool(payload.get("debug_browser", False)),
        )
        if not req.username:
            raise ScraperError("RA/usuário inválido.")
        if not req.password:
            raise ScraperError("Senha vazia.")

        self._emit_progress(AppStatus.LOGGING, "Iniciando navegador...")
        scraper = self._make_scraper(
            debug_browser=req.debug_browser,
            token=token,
            campus_name=req.campus_name,
        )
        await scraper.start()

        self._emit_progress(AppStatus.LOGGING, "Logando no portal...")
        login_result: LoginResult = await scraper.login(req.username, req.password, token=token)

        if login_result.manual_step_required and not req.debug_browser:
            # Requisito: se aparecer captcha/2FA, usar modo não-headless.
            self._emit_progress(AppStatus.LOGGING, "Captcha/2FA detectado. Reabrindo browser visível...")
            await scraper.force_close()
            scraper = self._make_scraper(
                debug_browser=True,
                token=token,
                campus_name=req.campus_name,
            )
            await scraper.start()
            login_result = await scraper.login(req.username, req.password, token=token)

        if login_result.manual_step_required:
            self.manual_step_required.emit(login_result.message)
            await self._wait_manual_continue(token)
            login_result = await scraper.continue_after_manual_step(token=token)

        if not login_result.ok:
            raise ScraperError(login_result.message)

        self._emit_progress(AppStatus.SCRAPING, "Abrindo Turmas Abertas...")
        req.portal_course_value, req.portal_course_label = await self._open_turmas_with_course_selection(
            scraper,
            token=token,
            preferred_value=req.portal_course_value,
            preferred_label=req.portal_course_label,
        )

        self._emit_progress(AppStatus.SCRAPING, "Raspando tabela de turmas (extração rápida via JS)...")
        turmas = await scraper.fetch_turmas_abertas(token=token)
        from src.core.storage import save_turmas_cache  # import local para evitar ciclo

        save_turmas_cache(turmas)
        self.turmas_ready.emit(turmas, "portal")
        self._emit_progress(AppStatus.READY, f"Turmas carregadas: {len(turmas)}")
        await scraper.close()

    async def _coro_refresh_with_session(self, payload: dict[str, Any], token: CancelToken) -> None:
        debug_browser = bool(payload.get("debug_browser", False))
        preferred_course_value = str(payload.get("portal_course_value", "")).strip() or None
        preferred_course_label = str(payload.get("portal_course_label", "")).strip() or None
        self._emit_progress(AppStatus.SCRAPING, "Abrindo sessão salva (storageState)...")
        scraper = self._make_scraper(
            debug_browser=debug_browser,
            token=token,
            campus_name=str(payload.get("campus_name", "Curitiba")),
        )
        await scraper.start()
        await self._open_turmas_with_course_selection(
            scraper,
            token=token,
            preferred_value=preferred_course_value,
            preferred_label=preferred_course_label,
        )
        self._emit_progress(AppStatus.SCRAPING, "Atualizando turmas...")
        turmas = await scraper.fetch_turmas_abertas(token=token)
        from src.core.storage import save_turmas_cache

        save_turmas_cache(turmas)
        self.turmas_ready.emit(turmas, "sessao")
        self._emit_progress(AppStatus.READY, f"Turmas atualizadas: {len(turmas)}")
        await scraper.close()


class ExportThread(QThread):
    """Thread simples para exportar PNG sem travar a UI."""

    done_ok = Signal(str)
    done_error = Signal(str)

    def __init__(
        self,
        *,
        result: ScheduleBuildResult,
        output_path: str,
        theme: str = "dark",
    ) -> None:
        super().__init__()
        self._result = result
        self._output_path = output_path
        self._theme = theme

    def run(self) -> None:  # noqa: D401
        try:
            subtitle = datetime.now().strftime("Gerado em %Y-%m-%d %H:%M")
            export_schedule_png(
                self._result,
                self._output_path,
                title="Grade UTFPR",
                subtitle=subtitle,
                theme=self._theme,
            )
            self.done_ok.emit(self._output_path)
        except Exception as exc:
            logger.exception("Falha ao exportar PNG")
            self.done_error.emit(str(exc))


class CourseSelectionDialog(QDialog):
    """Popup de seleção de curso para a etapa de Turmas Abertas."""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        message: str,
        options: list[dict[str, object]],
        selected_value: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Selecionar Curso")
        self.setModal(True)
        self.resize(640, 220)
        self.setMinimumWidth(540)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QLabel("Turmas Abertas exige seleção de curso")
        title.setObjectName("TitleLabel")
        layout.addWidget(title)

        info = QLabel(message or "Selecione um curso para continuar.")
        info.setObjectName("MutedLabel")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.course_combo = QComboBox()
        self.course_combo.setMinimumHeight(34)
        layout.addWidget(self.course_combo)

        for item in options:
            label = str(item.get("label", "")).strip()
            if not label:
                continue
            value = str(item.get("value", "")).strip()
            self.course_combo.addItem(label, value)

        if self.course_combo.count() == 0:
            self.course_combo.addItem("Nenhum curso encontrado", "")

        idx = -1
        if selected_value:
            idx = self.course_combo.findData(selected_value)
        if idx < 0:
            idx = 0
        self.course_combo.setCurrentIndex(idx)

        buttons = QHBoxLayout()
        buttons.addStretch(1)

        self.btn_cancel = QPushButton("Cancelar")
        self.btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(self.btn_cancel)

        self.btn_ok = QPushButton("Continuar")
        self.btn_ok.setObjectName("PrimaryButton")
        self.btn_ok.clicked.connect(self.accept)
        self.btn_ok.setEnabled(self.course_combo.count() > 0)
        buttons.addWidget(self.btn_ok)

        layout.addLayout(buttons)

    def selected_payload(self) -> dict[str, str]:
        value = self.course_combo.currentData()
        label = self.course_combo.currentText().strip()
        return {
            "portal_course_value": str(value).strip() if value not in (None, "") else "",
            "portal_course_label": label,
        }


class MainWindow(QMainWindow):
    """Janela principal (PySide6) com login, scraping, seleção e grade."""

    request_login_scrape = Signal(dict)
    request_refresh_session = Signal(dict)
    request_cancel_worker = Signal()
    request_continue_manual = Signal()
    request_submit_course_selection = Signal(dict)

    def __init__(self, *, smoke_ms: int | None = None) -> None:
        super().__init__()
        self.setWindowTitle("UTFPR Grade Builder")
        self.resize(1600, 940)
        self.setMinimumSize(1320, 820)

        self._app_state = load_app_state()
        self._turmas: list[Turma] = []
        self._schedule_result = ScheduleBuildResult.empty()
        self._password_mem = ""
        self._last_login_payload: dict[str, Any] | None = None
        self._busy = False
        self._current_status = AppStatus.IDLE
        self._export_thread: ExportThread | None = None

        self._build_ui()
        self._setup_worker_thread()
        self._connect_signals()
        self._apply_persisted_state()
        self._update_status(AppStatus.IDLE, "Aguardando login.")

        if smoke_ms:
            QTimer.singleShot(smoke_ms, self.close)

    # ---------- UI ----------
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("RootWidget")
        self.setCentralWidget(root)

        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(10)

        self.top_bar = QFrame()
        self.top_bar.setObjectName("TopBar")
        top_layout = QHBoxLayout(self.top_bar)
        top_layout.setContentsMargins(14, 10, 14, 10)
        top_layout.setSpacing(10)

        self.title_label = QLabel("UTFPR Grade Builder")
        self.title_label.setObjectName("TitleLabel")
        top_layout.addWidget(self.title_label)

        top_layout.addStretch(1)

        self.spinner = QProgressBar()
        self.spinner.setRange(0, 0)
        self.spinner.setMaximumWidth(140)
        self.spinner.hide()
        top_layout.addWidget(self.spinner)

        self.status_label = QLabel("IDLE")
        self.status_label.setObjectName("StatusBadge")
        top_layout.addWidget(self.status_label)

        self.status_msg = QLabel("Aguardando login.")
        self.status_msg.setObjectName("MutedLabel")
        top_layout.addWidget(self.status_msg)

        root_layout.addWidget(self.top_bar)

        self.stack = QStackedWidget()
        root_layout.addWidget(self.stack, 1)

        # Página Login
        login_page = QWidget()
        login_layout = QVBoxLayout(login_page)
        login_layout.setContentsMargins(0, 0, 0, 0)
        login_layout.addStretch(1)
        self.login_panel = LoginPanel()
        login_layout.addWidget(self.login_panel, 0, Qt.AlignHCenter)
        login_layout.addStretch(2)
        self.stack.addWidget(login_page)

        # Página principal
        main_page = QWidget()
        main_layout = QVBoxLayout(main_page)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        self.splitter = QSplitter(Qt.Horizontal)
        self.turmas_panel = TurmasPanel()
        self.grade_panel = GradePanel()
        self.splitter.addWidget(self.turmas_panel)
        self.splitter.addWidget(self.grade_panel)
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setSizes([560, 900])
        main_layout.addWidget(self.splitter)
        self.stack.addWidget(main_page)

        self.stack.setCurrentIndex(0)

        self.menuBar().clear()

    def _apply_persisted_state(self) -> None:
        self.login_panel.set_defaults(
            add_prefix_a=self._app_state.add_prefix_a,
            debug_browser=self._app_state.debug_browser,
            campus_name=self._app_state.campus_name,
        )
        self.turmas_panel.set_credit_limit(self._app_state.credit_limit)

    # ---------- Worker thread ----------
    def _setup_worker_thread(self) -> None:
        self._scrape_thread = QThread(self)
        self._scrape_thread.setObjectName("ScrapeThread")
        self._scrape_worker = ScrapeWorker()
        self._scrape_worker.moveToThread(self._scrape_thread)
        self._scrape_thread.start()

        self.request_login_scrape.connect(self._scrape_worker.run_login_and_scrape, Qt.QueuedConnection)
        self.request_refresh_session.connect(
            self._scrape_worker.run_refresh_with_session, Qt.QueuedConnection
        )
        self.request_cancel_worker.connect(self._scrape_worker.cancel, Qt.QueuedConnection)
        self.request_continue_manual.connect(
            self._scrape_worker.continue_after_manual_step, Qt.QueuedConnection
        )
        self.request_submit_course_selection.connect(
            self._scrape_worker.submit_course_selection, Qt.QueuedConnection
        )

        self._scrape_worker.progress.connect(self._on_worker_progress)
        self._scrape_worker.manual_step_required.connect(self._on_manual_step_required)
        self._scrape_worker.course_selection_required.connect(self._on_course_selection_required)
        self._scrape_worker.turmas_ready.connect(self._on_turmas_ready)
        self._scrape_worker.error.connect(self._on_worker_error)
        self._scrape_worker.task_finished.connect(self._on_worker_task_finished)

    def _connect_signals(self) -> None:
        self.login_panel.login_requested.connect(self._on_login_requested)
        self.login_panel.cancel_requested.connect(self._cancel_running_task)
        self.login_panel.continue_manual_requested.connect(self._continue_manual_step)

        self.turmas_panel.generate_requested.connect(self._generate_schedule)
        self.turmas_panel.clear_requested.connect(self._clear_selection)
        self.turmas_panel.report_requested.connect(self._show_report)
        self.turmas_panel.refresh_requested.connect(self._refresh_turmas)
        self.turmas_panel.back_requested.connect(self._go_back_to_login)
        self.turmas_panel.cancel_requested.connect(self._cancel_running_task)
        self.turmas_panel.open_logs_requested.connect(self._open_logs)
        self.turmas_panel.selection_changed.connect(self._rebuild_schedule)
        self.turmas_panel.credit_limit_changed.connect(lambda _v: self._rebuild_schedule())

        self.grade_panel.export_requested.connect(self._export_png)

    # ---------- Status / busy ----------
    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.spinner.setVisible(busy)
        self.login_panel.set_busy(busy and self.stack.currentIndex() == 0)
        self.turmas_panel.set_busy(busy and self.stack.currentIndex() == 1)
        self.grade_panel.set_busy(busy or (self._export_thread is not None and self._export_thread.isRunning()))

    def _update_status(self, status: AppStatus, message: str) -> None:
        self._current_status = status
        self.status_label.setText(status.value)
        self.status_label.setStyleSheet(status_badge_style(status))
        self.status_msg.setText(message)
        if self.stack.currentIndex() == 0:
            self.login_panel.set_status(message, error=(status == AppStatus.ERROR))
        else:
            self.turmas_panel.set_status(message, error=(status == AppStatus.ERROR))
            self.grade_panel.set_status(message, error=(status == AppStatus.ERROR))

    # ---------- Persistência ----------
    def _collect_state(self) -> AppState:
        self._app_state.selected_ids = sorted(self.turmas_panel.get_selected_ids())
        self._app_state.credit_limit = self.turmas_panel.get_credit_limit()
        self._app_state.add_prefix_a = self.login_panel.prefix_check.isChecked()
        self._app_state.debug_browser = self.login_panel.debug_check.isChecked()
        self._app_state.campus_name = self.login_panel.campus_combo.currentText().strip()
        if self.login_panel.course_combo.isVisible() and self.login_panel.course_combo.count() > 0:
            value = self.login_panel.course_combo.currentData()
            label = self.login_panel.course_combo.currentText().strip()
            self._app_state.portal_course_value = str(value).strip() if value not in (None, "") else None
            self._app_state.portal_course_label = label or self._app_state.portal_course_label
        return self._app_state

    def _save_state(self) -> None:
        with contextlib.suppress(Exception):
            save_app_state(self._collect_state())

    # ---------- Ações UI ----------
    def _on_login_requested(self, payload: dict[str, object]) -> None:
        ra = str(payload.get("ra", "")).strip()
        password = str(payload.get("password", ""))
        if not ra:
            QMessageBox.warning(self, "Login", "Informe o RA.")
            return
        if not password:
            QMessageBox.warning(self, "Login", "Informe a senha.")
            return
        if self._busy:
            QMessageBox.information(self, "Aguarde", "Já existe uma operação em andamento.")
            return

        self._password_mem = password  # somente em memória
        self._last_login_payload = dict(payload)
        self._set_busy(True)
        self.login_panel.show_manual_continue(False)
        self.login_panel.show_course_selection(False)
        self._update_status(AppStatus.LOGGING, "Enviando login para worker...")
        self.request_login_scrape.emit(
            {
                "ra": ra,
                "password": password,
                "campus_name": str(payload.get("campus_name", "Curitiba")),
                "portal_course_value": self._app_state.portal_course_value or "",
                "portal_course_label": self._app_state.portal_course_label or "",
                "add_prefix_a": bool(payload.get("add_prefix_a", True)),
                "debug_browser": bool(payload.get("debug_browser", False)),
            }
        )

    def _load_cache_json(self) -> None:
        initial_dir = (
            str(Path(self._app_state.last_cache_path).parent)
            if self._app_state.last_cache_path
            else str(Path.cwd())
        )
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Selecionar cache JSON",
            initial_dir,
            "JSON (*.json);;Todos (*.*)",
        )
        if not path:
            return
        try:
            turmas = load_turmas_cache(path)
        except Exception as exc:
            logger.exception("Falha ao carregar cache JSON")
            QMessageBox.critical(self, "Erro ao carregar cache", str(exc))
            self._update_status(AppStatus.ERROR, f"Falha ao carregar cache: {exc}")
            return
        self._app_state.last_cache_path = path
        self._apply_loaded_turmas(turmas, source="cache")
        self._update_status(AppStatus.READY, f"Cache carregado: {len(turmas)} turmas")

    def _cancel_running_task(self) -> None:
        if not self._busy:
            return
        self._update_status(AppStatus.CANCELED, "Cancelando operação...")
        self.request_cancel_worker.emit()

    def _continue_manual_step(self) -> None:
        self.login_panel.show_manual_continue(False)
        self._update_status(AppStatus.LOGGING, "Continuando após etapa manual...")
        self.request_continue_manual.emit()

    def _on_course_selection_submitted(self, payload: dict[str, object]) -> None:
        value = str(payload.get("portal_course_value", "")).strip()
        label = str(payload.get("portal_course_label", "")).strip()
        self._app_state.portal_course_value = value or None
        self._app_state.portal_course_label = label or self._app_state.portal_course_label
        if self._last_login_payload is not None:
            self._last_login_payload["portal_course_value"] = value
            self._last_login_payload["portal_course_label"] = label
        self._save_state()
        self.login_panel.show_course_selection(False)
        self._update_status(AppStatus.SCRAPING, "Curso enviado ao worker. Carregando Turmas Abertas...")
        self.request_submit_course_selection.emit(
            {
                "portal_course_value": value,
                "portal_course_label": label,
            }
        )

    def _open_logs(self) -> None:
        try:
            open_logs_folder()
        except Exception as exc:
            QMessageBox.warning(self, "Logs", f"Não foi possível abrir a pasta de logs:\n{exc}")

    def _refresh_turmas(self) -> None:
        if self._busy:
            QMessageBox.information(self, "Aguarde", "Já existe uma operação em andamento.")
            return
        if self._last_login_payload and self._password_mem:
            payload = dict(self._last_login_payload)
            payload["password"] = self._password_mem
            self._set_busy(True)
            self._update_status(AppStatus.SCRAPING, "Atualizando turmas via login em memória...")
            self.request_login_scrape.emit(payload)
            return
        if DEFAULT_STORAGE_STATE_PATH.exists():
            self._set_busy(True)
            self._update_status(AppStatus.SCRAPING, "Atualizando turmas via sessão salva...")
            self.request_refresh_session.emit(
                {
                    "debug_browser": self.login_panel.debug_check.isChecked(),
                    "campus_name": self.login_panel.campus_combo.currentText().strip(),
                    "portal_course_value": self._app_state.portal_course_value or "",
                    "portal_course_label": self._app_state.portal_course_label or "",
                }
            )
            return
        QMessageBox.warning(
            self,
            "Atualizar",
            "Sem credenciais em memória e sem storageState. Faça login novamente ou carregue cache JSON.",
        )

    def _go_back_to_login(self) -> None:
        self._save_state()
        self.stack.setCurrentIndex(0)
        self.login_panel.show_course_selection(False)
        self.login_panel.show_manual_continue(False)
        self._set_busy(False)
        self._update_status(AppStatus.IDLE, "Voltou para o login.")

    # ---------- Grade / seleção ----------
    def _apply_loaded_turmas(self, turmas: list[Turma], *, source: str) -> None:
        self._turmas = turmas
        selected = set(self._app_state.selected_ids)
        valid_selected = {t.uid() for t in turmas if t.uid() in selected}
        self.turmas_panel.set_turmas(turmas, selected_ids=valid_selected)
        self.stack.setCurrentIndex(1)
        self._rebuild_schedule()
        self.turmas_panel.set_status(f"Turmas carregadas via {source}: {len(turmas)}")
        self.grade_panel.set_status("Pronto.")

    def _selected_turmas(self) -> list[Turma]:
        return selected_turmas(self._turmas, self.turmas_panel.get_selected_ids())

    def _rebuild_schedule(self) -> None:
        self._schedule_result = build_schedule(self._selected_turmas())
        self.turmas_panel.update_schedule_info(self._schedule_result)
        self.grade_panel.set_schedule_result(self._schedule_result)
        limite = self.turmas_panel.get_credit_limit()
        if self._schedule_result.creditos_usados > limite:
            self._update_status(
                AppStatus.ERROR,
                f"Limite excedido ({self._schedule_result.creditos_usados}/{limite}).",
            )
        elif self.stack.currentIndex() == 1:
            self._update_status(
                AppStatus.READY,
                f"Grade pronta | Créditos: {self._schedule_result.creditos_usados} | Conflitos: {len(self._schedule_result.conflitos)}",
            )
        self._save_state()

    def _generate_schedule(self) -> None:
        self._rebuild_schedule()
        if self._schedule_result.conflitos:
            QMessageBox.warning(
                self,
                "Conflitos detectados",
                f"Foram detectados {len(self._schedule_result.conflitos)} conflitos de horário.",
            )

    def _clear_selection(self) -> None:
        self.turmas_panel.clear_selection()
        self._rebuild_schedule()

    def _show_report(self) -> None:
        self._rebuild_schedule()
        text = summarize_selection(self._selected_turmas(), self._schedule_result)
        dlg = ReportDialog(self, text=text)
        dlg.exec()

    def _export_png(self) -> None:
        self._rebuild_schedule()
        if not self._selected_turmas():
            QMessageBox.warning(self, "Exportar", "Nenhuma turma selecionada.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Salvar grade como PNG",
            default_png_name(),
            "PNG (*.png)",
        )
        if not path:
            return
        if self._export_thread is not None and self._export_thread.isRunning():
            QMessageBox.information(self, "Exportar", "Já existe uma exportação em andamento.")
            return
        self._export_thread = ExportThread(result=self._schedule_result, output_path=path, theme="dark")
        self._export_thread.done_ok.connect(self._on_export_ok)
        self._export_thread.done_error.connect(self._on_export_error)
        self._export_thread.finished.connect(self._on_export_finished)
        self.grade_panel.set_busy(True)
        self._update_status(AppStatus.SCRAPING, "Exportando PNG...")
        self._export_thread.start()

    def _on_export_ok(self, path: str) -> None:
        self._update_status(AppStatus.READY, f"PNG salvo em: {path}")
        QMessageBox.information(self, "Exportação", f"Imagem salva em:\n{path}")

    def _on_export_error(self, message: str) -> None:
        self._update_status(AppStatus.ERROR, f"Falha ao exportar PNG: {message}")
        QMessageBox.critical(self, "Erro ao exportar", message)

    def _on_export_finished(self) -> None:
        self.grade_panel.set_busy(False)

    # ---------- Sinais do worker ----------
    @Slot(object)
    def _on_worker_progress(self, progress: ProgressInfo) -> None:
        self._update_status(progress.status, progress.message)

    @Slot(str)
    def _on_manual_step_required(self, message: str) -> None:
        self.login_panel.show_course_selection(False)
        self.login_panel.show_manual_continue(True)
        self._update_status(AppStatus.LOGGING, message)
        QMessageBox.information(self, "Ação manual necessária", "Conclua manualmente e clique em Continuar.")

    @Slot(object, str)
    def _on_course_selection_required(self, payload_obj: object, message: str) -> None:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        options = payload.get("options", []) if isinstance(payload, dict) else []
        selected_value = payload.get("selected_value") if isinstance(payload, dict) else None
        self.login_panel.show_manual_continue(False)
        self.login_panel.show_course_selection(False)
        self._update_status(AppStatus.SCRAPING, message)

        dialog = CourseSelectionDialog(
            self,
            message=message,
            options=[item for item in options if isinstance(item, dict)],
            selected_value=str(selected_value) if selected_value not in (None, "") else None,
        )
        if dialog.exec() == QDialog.Accepted:
            self._on_course_selection_submitted(dialog.selected_payload())
            return

        self._update_status(AppStatus.CANCELED, "Seleção de curso cancelada pelo usuário.")
        self._cancel_running_task()

    @Slot(object, str)
    def _on_turmas_ready(self, turmas_obj: object, source: str) -> None:
        turmas = list(turmas_obj) if isinstance(turmas_obj, list) else []
        self._apply_loaded_turmas(turmas, source=source)

    @Slot(str)
    def _on_worker_error(self, message: str) -> None:
        self._update_status(AppStatus.ERROR, message)
        QMessageBox.critical(self, "Erro", message)

    @Slot(str)
    def _on_worker_task_finished(self, _task_name: str) -> None:
        self._set_busy(False)
        self.login_panel.show_manual_continue(False)
        self.login_panel.show_course_selection(False)
        self._save_state()

    # ---------- Encerramento ----------
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._save_state()
        self.request_cancel_worker.emit()
        if self._export_thread is not None and self._export_thread.isRunning():
            self._export_thread.wait(1500)
        self._scrape_thread.quit()
        self._scrape_thread.wait(2000)
        super().closeEvent(event)

