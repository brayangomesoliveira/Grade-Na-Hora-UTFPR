from __future__ import annotations

import asyncio
import contextlib
import difflib
import html as html_lib
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin

from src.core.models import Turma
from src.core.schedule import parse_horarios
from src.infra import selectors
from src.infra.cancel_token import CancelToken, CancelledError
from src.infra.logger import make_debug_artifact_paths

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import (
        Error as PlaywrightError,
        Page,
        TimeoutError as PlaywrightTimeoutError,
        async_playwright,
    )
except Exception:  # pragma: no cover - ambiente sem playwright
    PlaywrightError = Exception  # type: ignore[assignment]
    PlaywrightTimeoutError = Exception  # type: ignore[assignment]
    Page = Any  # type: ignore[assignment,misc]
    async_playwright = None  # type: ignore[assignment]

try:  # pragma: no cover - opcional em runtime
    from rapidfuzz import fuzz as rf_fuzz
    from rapidfuzz import process as rf_process
except Exception:  # pragma: no cover
    rf_fuzz = None  # type: ignore[assignment]
    rf_process = None  # type: ignore[assignment]

try:  # pragma: no cover - opcional em runtime
    from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential
except Exception:  # pragma: no cover
    AsyncRetrying = None  # type: ignore[assignment]
    retry_if_exception_type = None  # type: ignore[assignment]
    stop_after_attempt = None  # type: ignore[assignment]
    wait_exponential = None  # type: ignore[assignment]


class PageLike(Protocol):
    url: str

    async def evaluate(self, expression: str, arg: Any | None = None) -> Any: ...

    def locator(self, selector: str): ...

    async def wait_for_selector(self, selector: str, *, timeout: int | None = None) -> Any: ...

    async def wait_for_function(self, expression: str, arg: Any | None = None, *, timeout: int | None = None) -> Any: ...


class ScraperError(RuntimeError):
    """Erro geral de login/navegação/scraping."""


class SelectorChangedError(ScraperError):
    """Portal mudou / seletor não encontrado."""


@dataclass(slots=True)
class LoginResult:
    ok: bool
    message: str
    manual_step_required: bool = False


@dataclass(slots=True)
class PortalCourseOption:
    value: str
    label: str
    selected: bool = False
    placeholder: bool = False


class CourseSelectionRequired(ScraperError):
    """Turmas Abertas abriu, mas exige selecao de curso antes da tabela."""

    def __init__(self, message: str, *, options: list[PortalCourseOption]) -> None:
        super().__init__(message)
        self.options = options


class TurmasNavState(str, Enum):
    UNKNOWN = "unknown"
    PORTAL_MENU = "portal_menu"
    TURMAS_TABLE = "turmas_table"
    TURMAS_COURSE_SELECT = "turmas_course_select"


class PortalFlowState(str, Enum):
    INIT = "INIT"
    PUBLIC_PORTAL_PAGE = "PUBLIC_PORTAL_PAGE"
    CAMPUS_SELECTED = "CAMPUS_SELECTED"
    LOGIN_PAGE = "LOGIN_PAGE"
    LOGGED_IN = "LOGGED_IN"
    PORTAL_MENU_READY = "PORTAL_MENU_READY"
    TURMAS_ABERTAS_ENTRY = "TURMAS_ABERTAS_ENTRY"
    TURMAS_ABERTAS_COURSE_SELECT = "TURMAS_ABERTAS_COURSE_SELECT"
    TURMAS_ABERTAS_OPEN = "TURMAS_ABERTAS_OPEN"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    CAPTCHA_OR_2FA = "CAPTCHA_OR_2FA"
    FAILED = "FAILED"


class UtfprScraperAsync:
    """Scraper assíncrono do portal UTFPR usando async_playwright.

    Regras de robustez:
    - Não usa `networkidle` como condição principal.
    - Trata popup ao entrar em "Turmas Abertas".
    - Procura iframe com tabela quando necessário.
    - Extrai linhas em lote via uma chamada JS por página.
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        timeout_ms: int = selectors.DEFAULT_TIMEOUT_MS,
        storage_state_path: str | Path | None = None,
        retries: int = selectors.STEP_RETRIES,
        default_campus_name: str | None = None,
    ) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.storage_state_path = Path(storage_state_path) if storage_state_path else None
        self.retries = max(0, retries)
        self.default_campus_name = (default_campus_name or selectors.DEFAULT_CAMPUS_NAME).strip()

        self._pw = None
        self._browser = None
        self._context = None
        self.page: Page | None = None
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._cancel_token: CancelToken | None = None
        self._active_table_context: PageLike | None = None
        self._flow_state = PortalFlowState.INIT
        self._flow_started_monotonic = time.monotonic()
        self._flow_timeout_s = 120.0
        self._flow_transition_count = 0
        self._flow_transition_limit = 80
        self._session_recovery_attempts = 0

    # ---------- Ciclo de vida ----------
    def bind_runtime(self, *, loop: asyncio.AbstractEventLoop, cancel_token: CancelToken) -> None:
        self._event_loop = loop
        self._cancel_token = cancel_token
        cancel_token.register_cancel_callback(self.request_force_close_threadsafe)

    async def start(self) -> None:
        if self.page is not None:
            return
        if async_playwright is None:
            raise ScraperError(
                "Playwright nao disponivel. Instale com `pip install playwright` e `playwright install chromium`."
            )
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)
        context_kwargs: dict[str, object] = {}
        if self.storage_state_path and self.storage_state_path.exists():
            context_kwargs["storage_state"] = str(self.storage_state_path)
        self._context = await self._browser.new_context(**context_kwargs)
        await self._context.route("**/*", self._route_handler)
        self._context.set_default_timeout(self.timeout_ms)
        self.page = await self._context.new_page()
        self._reset_flow_tracking()
        logger.info("Playwright async iniciado (headless=%s)", self.headless)
        await self._set_flow_state(PortalFlowState.INIT, step="start")

    async def close(self) -> None:
        self._active_table_context = None
        for attr in ("page", "_context", "_browser", "_pw"):
            obj = getattr(self, attr, None)
            if obj is None:
                continue
            try:
                if attr == "_pw":
                    await obj.stop()
                else:
                    await asyncio.wait_for(obj.close(), timeout=1.5)
            except Exception:
                logger.debug("Falha ao fechar %s", attr, exc_info=True)
            setattr(self, attr, None)

    async def force_close(self) -> None:
        """Fecha contexto/browser rapidamente (usado no cancelamento)."""
        try:
            await self.close()
        except Exception:
            logger.debug("Falha no force_close", exc_info=True)

    def request_force_close_threadsafe(self) -> None:
        loop = self._event_loop
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self.force_close(), loop)
        except Exception:
            logger.debug("Falha ao agendar force_close thread-safe", exc_info=True)

    async def _route_handler(self, route, request) -> None:
        # Acelera carregamento sem quebrar JS/layout (mantém scripts e CSS).
        if request.resource_type in {"image", "media", "font"}:
            await route.abort()
        else:
            await route.continue_()

    def _check_cancel(self, token: CancelToken | None) -> None:
        self._ensure_flow_guard()
        if token is not None:
            token.raise_if_cancelled()

    def _ensure_page(self) -> Page:
        if self.page is None:
            raise ScraperError("Pagina Playwright nao iniciada.")
        return self.page

    def _reset_flow_tracking(self) -> None:
        self._flow_state = PortalFlowState.INIT
        self._flow_started_monotonic = time.monotonic()
        self._flow_transition_count = 0
        self._session_recovery_attempts = 0

    def _flow_elapsed_ms(self) -> int:
        return int((time.monotonic() - self._flow_started_monotonic) * 1000)

    def _ensure_flow_guard(self) -> None:
        if (time.monotonic() - self._flow_started_monotonic) > self._flow_timeout_s:
            raise ScraperError(
                f"Guard rail acionado: fluxo excedeu {int(self._flow_timeout_s)}s "
                f"(estado={self._flow_state.value}, transicoes={self._flow_transition_count})"
            )
        if self._flow_transition_count > self._flow_transition_limit:
            raise ScraperError(
                f"Guard rail acionado: muitas transicoes de estado ({self._flow_transition_count}) "
                f"(estado={self._flow_state.value})"
            )

    async def _log_flow_event(
        self,
        *,
        step: str,
        attempt: int | None = None,
        detail: str | None = None,
        page: Page | None = None,
    ) -> None:
        ref_page = page or self.page
        url = ""
        title = ""
        if ref_page is not None:
            with contextlib.suppress(Exception):
                url = str(ref_page.url or "")
            with contextlib.suppress(Exception):
                title = ((await ref_page.title()) or "").strip()
        logger.info(
            "FLOW step=%s attempt=%s state=%s elapsed_ms=%d url=%s title=%s detail=%s",
            step,
            "-" if attempt is None else attempt,
            self._flow_state.value,
            self._flow_elapsed_ms(),
            url,
            title[:180],
            (detail or "")[:240],
        )

    async def _set_flow_state(
        self,
        state: PortalFlowState,
        *,
        step: str,
        detail: str | None = None,
        page: Page | None = None,
    ) -> None:
        if state != self._flow_state:
            self._flow_transition_count += 1
            self._flow_state = state
        self._ensure_flow_guard()
        await self._log_flow_event(step=step, detail=detail, page=page)

    async def _save_debug_artifacts(self, prefix: str) -> tuple[Path, Path]:
        page = self.page
        png_path, html_path = make_debug_artifact_paths(prefix)
        if page is not None:
            with contextlib.suppress(Exception):
                await page.screenshot(path=str(png_path), full_page=True)
            with contextlib.suppress(Exception):
                html_path.write_text(await page.content(), encoding="utf-8")
        logger.warning("Artefatos de debug salvos: %s | %s", png_path, html_path)
        return png_path, html_path

    # ---------- Helpers de retry ----------
    async def _retry(self, op_name: str, coro_factory, *, token: CancelToken | None = None):
        retry_errors = (PlaywrightTimeoutError, PlaywrightError, SelectorChangedError, ScraperError)
        if (
            AsyncRetrying is not None
            and stop_after_attempt is not None
            and wait_exponential is not None
            and retry_if_exception_type is not None
        ):
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self.retries + 1),
                wait=wait_exponential(multiplier=1, min=1, max=8),
                retry=retry_if_exception_type(retry_errors),
                reraise=True,
            ):
                self._check_cancel(token)
                await self._log_flow_event(step=f"retry:{op_name}", attempt=attempt.retry_state.attempt_number)
                try:
                    with attempt:
                        return await coro_factory()
                except retry_errors as exc:
                    logger.warning(
                        "%s falhou (tentativa %d/%d): %s",
                        op_name,
                        attempt.retry_state.attempt_number,
                        self.retries + 1,
                        exc,
                    )
                    raise

        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            self._check_cancel(token)
            await self._log_flow_event(step=f"retry:{op_name}", attempt=attempt + 1)
            try:
                return await coro_factory()
            except retry_errors as exc:
                last_exc = exc
                if attempt >= self.retries:
                    break
                backoff_ms = selectors.RETRY_BACKOFF_MS * (attempt + 1)
                logger.warning("%s falhou (tentativa %d). Retry em %d ms: %s", op_name, attempt + 1, backoff_ms, exc)
                await asyncio.sleep(backoff_ms / 1000)
        assert last_exc is not None
        raise last_exc

    # ---------- Login ----------
    async def _goto_login(self, token: CancelToken | None) -> None:
        page = self._ensure_page()
        self._check_cancel(token)
        # Fluxo real desejado: pagina publica da UTFPR -> cidade -> login -> portal.
        await page.goto(
            selectors.PORTAL_PUBLIC_ALUNO_URL,
            wait_until="domcontentloaded",
            timeout=self.timeout_ms,
        )
        await self._set_flow_state(
            PortalFlowState.PUBLIC_PORTAL_PAGE,
            step="goto_public_portal",
            detail="Portal do Aluno publico carregado",
            page=page,
        )

    async def _has_login_fields(self, page: Page) -> bool:
        with contextlib.suppress(Exception):
            user = page.locator(selectors.SELECTOR_USERNAME).first
            pwd = page.locator(selectors.SELECTOR_PASSWORD).first
            if await user.count() and await pwd.count():
                return True
        return False

    async def _looks_like_campus_selector_page(self, page: Page) -> bool:
        try:
            body = ((await page.text_content("body")) or "").lower()
        except Exception:
            return False
        if await self._has_login_fields(page):
            return False
        hits = sum(1 for city in selectors.CAMPUS_PAGE_CITY_KEYWORDS if city.lower() in body)
        return hits >= 3

    async def _select_default_campus_if_present(
        self,
        page: Page,
        *,
        token: CancelToken | None = None,
    ) -> bool:
        self._check_cancel(token)
        if not await self._looks_like_campus_selector_page(page):
            return False

        campus = self.default_campus_name or selectors.DEFAULT_CAMPUS_NAME
        logger.info("Pagina de campus detectada; selecionando campus padrao: %s", campus)

        with contextlib.suppress(Exception):
            await page.get_by_role("link", name=re.compile(re.escape(campus), re.IGNORECASE)).first.click()
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
            await asyncio.sleep(0.2)
            await self._set_flow_state(
                PortalFlowState.CAMPUS_SELECTED,
                step="select_campus",
                detail=f"Campus selecionado: {campus}",
                page=page,
            )
            return True

        with contextlib.suppress(Exception):
            await page.get_by_role("button", name=re.compile(re.escape(campus), re.IGNORECASE)).first.click()
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
            await asyncio.sleep(0.2)
            await self._set_flow_state(
                PortalFlowState.CAMPUS_SELECTED,
                step="select_campus",
                detail=f"Campus selecionado: {campus}",
                page=page,
            )
            return True

        with contextlib.suppress(Exception):
            await page.get_by_text(campus, exact=False).first.click()
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
            await asyncio.sleep(0.2)
            await self._set_flow_state(
                PortalFlowState.CAMPUS_SELECTED,
                step="select_campus",
                detail=f"Campus selecionado: {campus}",
                page=page,
            )
            return True

        script = """
        (campus) => {
          const norm = (s) => (s || "").replace(/\\s+/g, " ").trim().toLowerCase();
          const target = norm(campus);
          const candidates = Array.from(document.querySelectorAll("a, button, label, span, div"));
          for (const el of candidates) {
            const txt = norm(el.innerText || el.textContent || "");
            if (txt === target || txt.includes(target)) {
              el.click();
              return true;
            }
          }
          return false;
        }
        """
        clicked = False
        with contextlib.suppress(Exception):
            clicked = bool(await page.evaluate(script, campus))
        if clicked:
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
            await asyncio.sleep(0.2)
            await self._set_flow_state(
                PortalFlowState.CAMPUS_SELECTED,
                step="select_campus_js",
                detail=f"Campus selecionado via JS: {campus}",
                page=page,
            )
            return True

        raise SelectorChangedError(
            "Pagina de selecao de campus detectada, mas nao foi possivel clicar em "
            f"'{campus}'. Ajuste src/infra/selectors.py."
        )

    async def _looks_like_portal_aluno_page(self, page: Page) -> bool:
        with contextlib.suppress(Exception):
            if await page.locator(selectors.PORTAL_IFRAME_SELECTOR).count():
                return True
        with contextlib.suppress(Exception):
            if await page.locator(selectors.PORTAL_MENU_CONTAINER_SELECTOR).count():
                return True
        try:
            body = ((await page.text_content("body")) or "").lower()
        except Exception:
            return False
        return any(k in body for k in selectors.PORTAL_ALUNO_KEYWORDS)

    async def _looks_like_portal_home_shell_page(self, page: Page) -> bool:
        if await self._has_login_fields(page):
            return False
        if await self._looks_like_portal_aluno_page(page):
            return False
        try:
            body = ((await page.text_content("body")) or "").lower()
        except Exception:
            return False
        return any(k in body for k in selectors.PORTAL_HOME_SHELL_KEYWORDS)

    async def _click_portal_aluno_tab_if_present(self, page: Page, *, token: CancelToken | None = None) -> bool:
        self._check_cancel(token)
        tab_text = selectors.PORTAL_HOME_TAB_TEXT

        with contextlib.suppress(Exception):
            await page.get_by_role("link", name=re.compile(re.escape(tab_text), re.IGNORECASE)).first.click()
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("domcontentloaded", timeout=min(self.timeout_ms, 4000))
            await asyncio.sleep(0.35)
            return True

        with contextlib.suppress(Exception):
            await page.get_by_role("button", name=re.compile(re.escape(tab_text), re.IGNORECASE)).first.click()
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("domcontentloaded", timeout=min(self.timeout_ms, 4000))
            await asyncio.sleep(0.35)
            return True

        with contextlib.suppress(Exception):
            await page.get_by_text(tab_text, exact=False).first.click()
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("domcontentloaded", timeout=min(self.timeout_ms, 4000))
            await asyncio.sleep(0.35)
            return True

        script = """
        (tabText) => {
          const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
          const target = norm(tabText);
          const nodes = Array.from(document.querySelectorAll('a, button, li, div, span'));
          for (const el of nodes) {
            const txt = norm(el.innerText || el.textContent || '');
            if (txt !== target) continue;
            const clickable = el.closest('a,button,[onclick],li,div');
            (clickable || el).click();
            return true;
          }
          return false;
        }
        """
        with contextlib.suppress(Exception):
            if await page.evaluate(script, tab_text):
                with contextlib.suppress(Exception):
                    await page.wait_for_load_state("domcontentloaded", timeout=min(self.timeout_ms, 4000))
                await asyncio.sleep(0.35)
                return True
        return False

    async def _page_signature(self, page: Page) -> str:
        try:
            title = (await page.title()) or ""
        except Exception:
            title = ""
        try:
            body = ((await page.text_content("body")) or "")[:1200]
        except Exception:
            body = ""
        normalized = " ".join(body.split()).lower()
        return f"{title.strip().lower()}|{normalized}"

    async def _ensure_login_surface_or_portal(
        self,
        page: Page,
        *,
        token: CancelToken | None = None,
        max_steps: int = 14,
    ) -> str:
        """Resolve a sequência inicial: seleção de campus -> login ou portal já autenticado.

        Retorna:
        - `login`: campos usuário/senha visíveis
        - `portal`: página "Portal do Aluno" detectada (sessão já ativa)
        """
        campus_sig_counts: dict[str, int] = {}
        shell_sig_counts: dict[str, int] = {}

        for _step in range(max_steps):
            self._check_cancel(token)

            if await self._has_login_fields(page):
                logger.info("Superfície de login detectada")
                await self._set_flow_state(
                    PortalFlowState.LOGIN_PAGE,
                    step="detect_login_page",
                    detail="Campos usuario/senha visiveis",
                    page=page,
                )
                return "login"

            if await self._looks_like_portal_aluno_page(page):
                logger.info("Portal do Aluno detectado (sessão ativa ou pós-login)")
                await self._set_flow_state(
                    PortalFlowState.LOGGED_IN,
                    step="detect_portal_aluno",
                    detail="Portal do Aluno detectado",
                    page=page,
                )
                return "portal"

            if await self._looks_like_portal_home_shell_page(page):
                sig = await self._page_signature(page)
                shell_sig_counts[sig] = shell_sig_counts.get(sig, 0) + 1
                if shell_sig_counts[sig] > 3:
                    raise SelectorChangedError(
                        "Loop detectado na pagina inicial do sistemas2 ao abrir a aba "
                        "'Portal do Aluno'."
                    )
                if not await self._click_portal_aluno_tab_if_present(page, token=token):
                    raise SelectorChangedError(
                        "Pagina inicial do sistemas2 detectada, mas nao foi possivel clicar em "
                        "'Portal do Aluno'. Ajuste src/infra/selectors.py."
                    )
                continue

            if await self._looks_like_campus_selector_page(page):
                sig = await self._page_signature(page)
                campus_sig_counts[sig] = campus_sig_counts.get(sig, 0) + 1
                if campus_sig_counts[sig] > 3:
                    raise SelectorChangedError(
                        "Loop detectado na selecao de campus. A pagina de cidades reapareceu "
                        "repetidamente sem avancar para login/portal."
                    )
                await self._select_default_campus_if_present(page, token=token)
                with contextlib.suppress(Exception):
                    await page.wait_for_load_state("domcontentloaded", timeout=min(self.timeout_ms, 3500))
                await asyncio.sleep(0.35)
                continue

            # Fallback: em alguns cenários o login está em /login e a entrada redireciona tarde.
            with contextlib.suppress(Exception):
                await page.goto(selectors.LOGIN_URL, wait_until="domcontentloaded", timeout=min(self.timeout_ms, 4500))
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("domcontentloaded", timeout=min(self.timeout_ms, 2000))
            await asyncio.sleep(0.2)

        raise SelectorChangedError(
            "Nao foi possivel resolver a etapa inicial (cidade/login/portal). "
            "Ajuste src/infra/selectors.py para o fluxo atual do portal."
        )

    async def _fill_with_fallback(
        self,
        page: Page,
        *,
        css: str,
        fallback_labels: tuple[str, ...],
        value: str,
    ) -> None:
        try:
            locator = page.locator(css).first
            await locator.wait_for(state="visible", timeout=2500)
            await locator.fill(value)
            return
        except Exception:
            logger.debug("Falha em CSS '%s'; tentando fallback por label", css, exc_info=True)
        for label in fallback_labels:
            with contextlib.suppress(Exception):
                await page.get_by_label(label, exact=False).fill(value)
                return
        raise SelectorChangedError(f"Nao foi possivel localizar campo {fallback_labels}")

    async def _click_login(self, page: Page) -> None:
        try:
            await page.locator(selectors.SELECTOR_LOGIN_BUTTON).first.click()
            return
        except Exception:
            logger.debug("Falha no seletor de login; usando fallback por texto", exc_info=True)
        for txt in selectors.LOGIN_BUTTON_TEXTS:
            with contextlib.suppress(Exception):
                await page.get_by_role("button", name=re.compile(re.escape(txt), re.IGNORECASE)).first.click()
                return
            with contextlib.suppress(Exception):
                await page.get_by_text(txt, exact=False).first.click()
                return
        raise SelectorChangedError("Nao foi possivel localizar o botao de login")

    async def _manual_step_detected(self, page: Page) -> bool:
        try:
            body_text = ((await page.text_content("body")) or "").lower()
        except Exception:
            return False
        return any(k in body_text for k in selectors.MANUAL_STEP_KEYWORDS)

    async def _persist_storage_state(self) -> None:
        if self.storage_state_path and self._context is not None:
            self.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
            await self._context.storage_state(path=str(self.storage_state_path))

    async def login(self, username: str, password: str, *, token: CancelToken | None = None) -> LoginResult:
        page = self._ensure_page()

        async def _run() -> LoginResult:
            self._check_cancel(token)
            await self._goto_login(token)
            self._check_cancel(token)
            surface = await self._ensure_login_surface_or_portal(page, token=token)
            if surface == "portal":
                await self._persist_storage_state()
                return LoginResult(ok=True, message="Portal do Aluno ja estava ativo.")
            self._check_cancel(token)
            await self._fill_with_fallback(
                page,
                css=selectors.SELECTOR_USERNAME,
                fallback_labels=selectors.USERNAME_LABELS,
                value=username,
            )
            await self._fill_with_fallback(
                page,
                css=selectors.SELECTOR_PASSWORD,
                fallback_labels=selectors.PASSWORD_LABELS,
                value=password,
            )
            self._check_cancel(token)
            await self._click_login(page)
            # Evita networkidle; usa domcontentloaded + pequeno tempo de respiro.
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
            await asyncio.sleep(0.25)
            # Alguns fluxos podem retornar para seleção de campus após o submit.
            post_surface: str | None = None
            with contextlib.suppress(SelectorChangedError):
                post_surface = await self._ensure_login_surface_or_portal(page, token=token, max_steps=6)
            if post_surface == "login" and await self._has_login_fields(page):
                await self._set_flow_state(
                    PortalFlowState.FAILED,
                    step="login_post_submit",
                    detail="Formulario permaneceu na tela apos submit",
                    page=page,
                )
                return LoginResult(
                    ok=False,
                    message=(
                        "Nao foi possivel avancar apos o login (formulario permaneceu na tela). "
                        "Confirme campus/credenciais e tente novamente."
                    ),
                )
            if await self._manual_step_detected(page):
                await self._set_flow_state(
                    PortalFlowState.CAPTCHA_OR_2FA,
                    step="detect_captcha_or_2fa",
                    detail="Sinal de captcha/2FA detectado apos login",
                    page=page,
                )
                return LoginResult(
                    ok=False,
                    manual_step_required=True,
                    message="Conclua manualmente e clique em Continuar (captcha/2FA).",
                )
            await self._persist_storage_state()
            await self._set_flow_state(
                PortalFlowState.LOGGED_IN,
                step="login_success",
                detail="Login concluido",
                page=page,
            )
            return LoginResult(ok=True, message="Login realizado/enviado com sucesso.")

        try:
            return await self._retry("login", _run, token=token)
        except (CancelledError, PlaywrightError, PlaywrightTimeoutError, ScraperError, SelectorChangedError) as exc:
            await self._save_debug_artifacts("login_error")
            return LoginResult(ok=False, message=str(exc))

    async def continue_after_manual_step(self, *, token: CancelToken | None = None) -> LoginResult:
        page = self._ensure_page()
        self._check_cancel(token)
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
        await asyncio.sleep(0.2)
        if await self._manual_step_detected(page):
            await self._set_flow_state(
                PortalFlowState.CAPTCHA_OR_2FA,
                step="manual_step_pending",
                detail="Captcha/2FA ainda presente",
                page=page,
            )
            return LoginResult(
                ok=False,
                manual_step_required=True,
                message="Ainda ha indicios de captcha/2FA. Finalize no navegador e clique Continuar novamente.",
            )
        await self._persist_storage_state()
        await self._set_flow_state(
            PortalFlowState.LOGGED_IN,
            step="manual_step_done",
            detail="Etapa manual concluida",
            page=page,
        )
        return LoginResult(ok=True, message="Etapa manual concluida.")

    # ---------- Navegação para Turmas Abertas ----------
    async def _prepare_portal_menu_if_needed(self, page: Page, *, token: CancelToken | None = None) -> None:
        self._check_cancel(token)
        try:
            body = ((await page.text_content("body")) or "").lower()
        except Exception:
            return
        if not any(k in body for k in selectors.PORTAL_ALUNO_KEYWORDS):
            return

        with contextlib.suppress(Exception):
            await page.wait_for_selector(selectors.PORTAL_IFRAME_SELECTOR, timeout=2500)

        # Garante que o menu Ajax do curso foi inicializado (quando aplicável).
        with contextlib.suppress(Exception):
            await page.evaluate(
                "() => { if (typeof AjaxSelecionaCurso === 'function') { AjaxSelecionaCurso(1); return true; } return false; }"
            )

        # Espera o item de menu aparecer na área do menu Ajax (evita confundir com títulos da página).
        script = """
        (menuSelector, targetText) => {
          const root = document.querySelector(menuSelector);
          if (!root) return false;
          const norm = (s) => (s || "").replace(/\\s+/g, " ").trim().toLowerCase();
          const target = norm(targetText);
          return norm(root.innerText || root.textContent || "").includes(target);
        }
        """
        for txt in selectors.TURMAS_ABERTAS_TEXTS:
            found = False
            for _ in range(18):
                self._check_cancel(token)
                with contextlib.suppress(Exception):
                    found = bool(
                        await page.evaluate(script, selectors.PORTAL_MENU_CONTAINER_SELECTOR, txt)
                    )
                if found:
                    break
                await asyncio.sleep(0.2)
            if found:
                logger.info("Menu Ajax do Portal do Aluno carregado com item '%s'", txt)
                await self._set_flow_state(
                    PortalFlowState.PORTAL_MENU_READY,
                    step="portal_menu_ready",
                    detail=f"Item de menu visivel: {txt}",
                    page=page,
                )
                break

    def _all_page_contexts(self, page: Page) -> list[PageLike]:
        return [page, *[f for f in page.frames if f is not page.main_frame]]

    def _context_urls_snapshot(self, page: Page) -> tuple[str, ...]:
        urls: list[str] = []
        with contextlib.suppress(Exception):
            if page.url:
                urls.append(page.url)
        for frame in page.frames:
            if frame is page.main_frame:
                continue
            with contextlib.suppress(Exception):
                if frame.url:
                    urls.append(frame.url)
        return tuple(sorted(set(urls)))

    async def _ctx_looks_like_turmas_abertas(self, ctx: PageLike) -> bool:
        script = """
        (keywords) => {
          const text = ((document.body && (document.body.innerText || document.body.textContent)) || "")
            .replace(/\\s+/g, " ")
            .trim()
            .toLowerCase();
          if (document.querySelector("table td.t")) return true;
          if (document.querySelector("table[border='1']")) return true;
          for (const kw of (keywords || [])) {
            const k = (kw || "").toLowerCase();
            if (k && text.includes(k)) return true;
          }
          return false;
        }
        """
        with contextlib.suppress(Exception):
            return bool(await ctx.evaluate(script, list(selectors.PORTAL_TURMAS_PAGE_KEYWORDS)))
        return False

    async def _page_looks_like_turmas_abertas_anywhere(self, page: Page) -> bool:
        for ctx in self._all_page_contexts(page):
            if await self._ctx_looks_like_turmas_abertas(ctx):
                return True
        return False

    async def _ctx_content_html(self, ctx: PageLike) -> str | None:
        content_fn = getattr(ctx, "content", None)
        if not callable(content_fn):
            return None
        with contextlib.suppress(Exception):
            html_text = await content_fn()
            if isinstance(html_text, str) and html_text.strip():
                return html_text
        return None

    @classmethod
    def _html_text(cls, fragment: str) -> str:
        text = re.sub(r"(?is)<br\s*/?>", " ", fragment)
        text = re.sub(r"(?is)<[^>]+>", " ", text)
        text = html_lib.unescape(text).replace("\xa0", " ")
        return " ".join(text.split())

    @classmethod
    def _extract_course_options_from_html_source(cls, html_text: str) -> list[PortalCourseOption]:
        pattern = re.compile(selectors.PORTAL_TURMAS_COURSE_OPTION_PATTERN)
        placeholders = {cls._norm(p) for p in selectors.PORTAL_TURMAS_COURSE_PLACEHOLDER_TEXTS}
        best_options: list[PortalCourseOption] = []
        best_score = -1

        for match in re.finditer(
            r"(?is)<select\b(?P<attrs>[^>]*)>(?P<body>.*?)</select>",
            html_text,
        ):
            attrs = match.group("attrs") or ""
            body = match.group("body") or ""
            opts: list[PortalCourseOption] = []
            for opt_match in re.finditer(
                r"(?is)<option\b(?P<attrs>[^>]*)>(?P<label>.*?)</option>",
                body,
            ):
                opt_attrs = opt_match.group("attrs") or ""
                raw_label = opt_match.group("label") or ""
                label = cls._html_text(raw_label)
                val_match = re.search(r'(?is)\bvalue\s*=\s*["\']?([^"\'>\s]*)', opt_attrs)
                value = (val_match.group(1) if val_match else "").strip()
                selected = bool(re.search(r"(?i)\bselected\b", opt_attrs))
                placeholder = cls._norm(label) in placeholders or not pattern.search(label or "")
                opts.append(
                    PortalCourseOption(
                        value=value,
                        label=label,
                        selected=selected,
                        placeholder=placeholder,
                    )
                )

            course_like = [o for o in opts if o.label and not o.placeholder]
            if len(course_like) < 2:
                continue

            score = len(course_like)
            if re.search(r"(?i)\b(id|name)\s*=\s*[\"'][^\"']*(cur|curso)", attrs):
                score += 20
            if score > best_score:
                best_score = score
                best_options = opts

        return best_options

    async def _extract_course_options_from_ctx(self, ctx: PageLike) -> list[PortalCourseOption]:
        script = """
        (patternText, placeholderTexts) => {
          const norm = (s) => (s || "").replace(/\\s+/g, " ").trim().toLowerCase();
          const placeholders = new Set((placeholderTexts || []).map(norm));
          let re = null;
          try { re = new RegExp(patternText || ""); } catch (_) { re = /^\\s*\\d{3,4}\\s*-\\s*/; }
          const visible = (el) => {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            const st = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
          };
          const selects = Array.from(document.querySelectorAll("select")).filter(visible);
          let best = null;
          let bestScore = -1;
          for (const sel of selects) {
            const opts = Array.from(sel.options || []).map((opt) => {
              const label = (opt.textContent || opt.innerText || "").replace(/\\s+/g, " ").trim();
              const value = String(opt.value || "").trim();
              const placeholder = placeholders.has(norm(label)) || !re.test(label || "");
              return { value, label, selected: !!opt.selected, placeholder };
            });
            const courseLike = opts.filter((o) => !o.placeholder);
            if (courseLike.length < 2) continue;
            let score = courseLike.length;
            if (sel.name && /cur|curso/i.test(sel.name)) score += 10;
            if (sel.id && /cur|curso/i.test(sel.id)) score += 10;
            if (opts.some((o) => o.selected && !o.placeholder)) score += 3;
            if ((sel.size || 0) > 1) score += 1;
            if (score > bestScore) {
              bestScore = score;
              best = opts;
            }
          }
          return best || [];
        }
        """
        rows: list[dict[str, Any]] = []
        with contextlib.suppress(Exception):
            rows = await ctx.evaluate(
                script,
                selectors.PORTAL_TURMAS_COURSE_OPTION_PATTERN,
                list(selectors.PORTAL_TURMAS_COURSE_PLACEHOLDER_TEXTS),
            )
        out: list[PortalCourseOption] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            out.append(
                PortalCourseOption(
                    value=str(row.get("value", "")).strip(),
                    label=str(row.get("label", "")).strip(),
                    selected=bool(row.get("selected", False)),
                    placeholder=bool(row.get("placeholder", False)),
                )
            )
        if out:
            return out

        html_text = await self._ctx_content_html(ctx)
        if not html_text:
            return []

        html_options = self._extract_course_options_from_html_source(html_text)
        if html_options:
            logger.info(
                "Curso em Turmas Abertas detectado via codigo-fonte HTML (%d opcoes)",
                len(html_options),
            )
        return html_options

    async def _find_course_select_context(
        self,
        page: Page,
        *,
        token: CancelToken | None = None,
    ) -> tuple[PageLike, list[PortalCourseOption]] | None:
        for ctx in self._all_page_contexts(page):
            self._check_cancel(token)
            options = await self._extract_course_options_from_ctx(ctx)
            if options:
                return (ctx, options)
        return None

    async def _page_has_course_select_anywhere(self, page: Page, *, token: CancelToken | None = None) -> bool:
        return (await self._find_course_select_context(page, token=token)) is not None

    async def _detect_turmas_nav_state(
        self,
        page: Page,
        *,
        token: CancelToken | None = None,
    ) -> TurmasNavState:
        if await self._find_frame_with_table(page, token=token):
            return TurmasNavState.TURMAS_TABLE
        if await self._page_has_course_select_anywhere(page, token=token):
            return TurmasNavState.TURMAS_COURSE_SELECT
        if await self._looks_like_portal_aluno_page(page):
            return TurmasNavState.PORTAL_MENU
        return TurmasNavState.UNKNOWN

    async def list_portal_course_options(
        self,
        *,
        token: CancelToken | None = None,
    ) -> list[PortalCourseOption]:
        page = self._ensure_page()
        found = await self._find_course_select_context(page, token=token)
        if not found:
            return []
        _, options = found
        return options

    @classmethod
    def choose_portal_course_option(
        cls,
        options: list[PortalCourseOption],
        *,
        preferred_value: str | None = None,
        preferred_label: str | None = None,
    ) -> PortalCourseOption | None:
        meaningful = [o for o in options if not o.placeholder and o.label]
        if not meaningful:
            return None

        if preferred_value:
            wanted = preferred_value.strip()
            for opt in meaningful:
                if opt.value.strip() == wanted:
                    return opt

        if preferred_label:
            wanted_label = cls._norm(preferred_label)
            for opt in meaningful:
                if cls._norm(opt.label) == wanted_label:
                    return opt
            for opt in meaningful:
                if wanted_label and wanted_label in cls._norm(opt.label):
                    return opt
            labels = [opt.label for opt in meaningful]
            fuzzy_label, fuzzy_score = cls._fuzzy_best_label_match(preferred_label, labels)
            if fuzzy_label and fuzzy_score >= 78:
                for opt in meaningful:
                    if opt.label == fuzzy_label:
                        logger.info(
                            "Curso escolhido por matching tolerante: '%s' (score=%s)",
                            fuzzy_label,
                            fuzzy_score,
                        )
                        return opt

        for opt in meaningful:
            if opt.selected:
                return opt

        if len(meaningful) == 1:
            return meaningful[0]
        return None

    @classmethod
    def _fuzzy_best_label_match(cls, target: str, labels: list[str]) -> tuple[str | None, float]:
        if not target or not labels:
            return (None, 0.0)
        norm_target = cls._norm(target)
        if not norm_target:
            return (None, 0.0)
        norm_map = {label: cls._norm(label) for label in labels if label}
        if not norm_map:
            return (None, 0.0)

        if rf_process is not None and rf_fuzz is not None:
            match = rf_process.extractOne(
                norm_target,
                list(norm_map.values()),
                scorer=rf_fuzz.WRatio,
            )
            if match:
                best_norm = str(match[0])
                score = float(match[1])
                for label, normalized in norm_map.items():
                    if normalized == best_norm:
                        return (label, score)

        candidates = list(norm_map.values())
        best_norm = difflib.get_close_matches(norm_target, candidates, n=1, cutoff=0.5)
        if not best_norm:
            return (None, 0.0)
        ratio = difflib.SequenceMatcher(None, norm_target, best_norm[0]).ratio() * 100
        for label, normalized in norm_map.items():
            if normalized == best_norm[0]:
                return (label, ratio)
        return (None, 0.0)

    async def _set_course_select_value_in_ctx(
        self,
        ctx: PageLike,
        *,
        course_value: str | None = None,
        course_label: str | None = None,
    ) -> str | None:
        script = """
        (courseValue, courseLabel, patternText, placeholderTexts) => {
          const norm = (s) => (s || "").replace(/\\s+/g, " ").trim().toLowerCase();
          const placeholders = new Set((placeholderTexts || []).map(norm));
          let re = null;
          try { re = new RegExp(patternText || ""); } catch (_) { re = /^\\s*\\d{3,4}\\s*-\\s*/; }
          const visible = (el) => {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            const st = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
          };
          const selects = Array.from(document.querySelectorAll("select")).filter(visible);
          let best = null;
          let bestScore = -1;
          for (const sel of selects) {
            const opts = Array.from(sel.options || []);
            const courseLikeCount = opts.filter((opt) => {
              const label = (opt.textContent || opt.innerText || "").replace(/\\s+/g, " ").trim();
              return label && !placeholders.has(norm(label)) && re.test(label);
            }).length;
            if (courseLikeCount < 2) continue;
            let score = courseLikeCount;
            if (sel.name && /cur|curso/i.test(sel.name)) score += 10;
            if (sel.id && /cur|curso/i.test(sel.id)) score += 10;
            if (score > bestScore) { best = sel; bestScore = score; }
          }
          if (!best) return null;
          const desiredValue = String(courseValue || "").trim();
          const desiredLabel = norm(courseLabel || "");
          let target = null;
          if (desiredValue) {
            target = Array.from(best.options || []).find((opt) => String(opt.value || "").trim() === desiredValue) || null;
          }
          if (!target && desiredLabel) {
            target = Array.from(best.options || []).find((opt) => {
              const label = (opt.textContent || opt.innerText || "").replace(/\\s+/g, " ").trim();
              const n = norm(label);
              return n === desiredLabel || n.includes(desiredLabel);
            }) || null;
          }
          if (!target) return null;
          best.value = String(target.value || "");
          for (const ev of ["input", "change"]) {
            try { best.dispatchEvent(new Event(ev, { bubbles: true })); } catch (_) {}
          }
          return (target.textContent || target.innerText || "").replace(/\\s+/g, " ").trim() || null;
        }
        """
        with contextlib.suppress(Exception):
            return await ctx.evaluate(
                script,
                course_value or "",
                course_label or "",
                selectors.PORTAL_TURMAS_COURSE_OPTION_PATTERN,
                list(selectors.PORTAL_TURMAS_COURSE_PLACEHOLDER_TEXTS),
            )
        return None

    async def select_portal_course(
        self,
        *,
        course_value: str | None = None,
        course_label: str | None = None,
        token: CancelToken | None = None,
    ) -> str:
        page = self._ensure_page()
        self._check_cancel(token)
        selected_label: str | None = None

        async def _attempt_select_and_wait() -> None:
            nonlocal selected_label
            selected_label = None
            for ctx in self._all_page_contexts(page):
                self._check_cancel(token)
                selected_label = await self._set_course_select_value_in_ctx(
                    ctx,
                    course_value=course_value,
                    course_label=course_label,
                )
                if selected_label:
                    break
            if not selected_label:
                raise SelectorChangedError("Nao foi possivel selecionar o curso em Turmas Abertas.")
            logger.info("Curso selecionado em Turmas Abertas: %s", selected_label)
            await self.ensure_turmas_table_ready(token=token)

        if (
            AsyncRetrying is not None
            and stop_after_attempt is not None
            and wait_exponential is not None
            and retry_if_exception_type is not None
        ):
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=1, max=8),
                retry=retry_if_exception_type((PlaywrightTimeoutError, PlaywrightError, SelectorChangedError)),
                reraise=True,
            ):
                self._check_cancel(token)
                try:
                    with attempt:
                        await _attempt_select_and_wait()
                except (PlaywrightTimeoutError, PlaywrightError, SelectorChangedError) as exc:
                    logger.warning(
                        "Selecao de curso falhou (tentativa %d/3): %s",
                        attempt.retry_state.attempt_number,
                        exc,
                    )
                    raise
        else:
            await _attempt_select_and_wait()

        assert selected_label is not None
        return selected_label

    async def ensure_turmas_table_ready(self, *, token: CancelToken | None = None) -> None:
        page = self._ensure_page()
        self._check_cancel(token)
        if await self._has_login_fields(page):
            await self._set_flow_state(
                PortalFlowState.SESSION_EXPIRED,
                step="ensure_turmas_table_ready",
                detail="Retorno silencioso ao login detectado",
                page=page,
            )
            raise ScraperError("Sessao expirada ao abrir Turmas Abertas (retorno ao login).")
        quick_timeout = min(self.timeout_ms, 2200)
        after_confirm_timeout = min(self.timeout_ms, 5000)

        ctx = await self._find_frame_with_table(page, token=token, timeout_ms=quick_timeout)
        if ctx is not None:
            self._active_table_context = ctx
            await self._set_flow_state(
                PortalFlowState.TURMAS_ABERTAS_OPEN,
                step="turmas_table_ready",
                detail="Tabela de turmas localizada",
                page=page,
            )
            return

        found = await self._find_course_select_context(page, token=token)
        if found is not None:
            _, options = found
            await self._set_flow_state(
                PortalFlowState.TURMAS_ABERTAS_COURSE_SELECT,
                step="turmas_course_select",
                detail=f"Selecao de curso requerida ({len(options)} opcoes)",
                page=page,
            )
            raise CourseSelectionRequired(
                "Selecione um curso para carregar Turmas Abertas.",
                options=options,
            )

        with contextlib.suppress(Exception):
            clicked = await self._maybe_click_confirm_anywhere(page, token=token)
            if clicked:
                await asyncio.sleep(0.35)

        ctx = await self._find_frame_with_table(page, token=token, timeout_ms=after_confirm_timeout)
        if ctx is not None:
            self._active_table_context = ctx
            await self._set_flow_state(
                PortalFlowState.TURMAS_ABERTAS_OPEN,
                step="turmas_table_ready_after_confirm",
                detail="Tabela de turmas localizada apos confirmar",
                page=page,
            )
            return

        found = await self._find_course_select_context(page, token=token)
        if found is not None:
            _, options = found
            await self._set_flow_state(
                PortalFlowState.TURMAS_ABERTAS_COURSE_SELECT,
                step="turmas_course_select_after_confirm",
                detail=f"Selecao de curso requerida apos confirmar ({len(options)} opcoes)",
                page=page,
            )
            raise CourseSelectionRequired(
                "A tela de Turmas Abertas abriu a lista de cursos. Escolha um curso para continuar.",
                options=options,
            )

        await self._set_flow_state(
            PortalFlowState.FAILED,
            step="turmas_table_not_found",
            detail="Nao encontrou tabela nem seletor de curso em Turmas Abertas",
            page=page,
        )
        await self._save_debug_artifacts("turmas_anchor_error")
        raise SelectorChangedError(
            "Nao foi possivel localizar a tabela de Turmas Abertas (page/iframe). "
            "Ajuste src/infra/selectors.py."
        )

    async def _wait_turmas_open_after_click(
        self,
        page: Page,
        *,
        baseline_urls: tuple[str, ...],
        token: CancelToken | None = None,
        timeout_ms: int = 3500,
    ) -> bool:
        loops = max(1, timeout_ms // 150)
        for _ in range(loops):
            self._check_cancel(token)
            if await self._page_looks_like_turmas_abertas_anywhere(page):
                return True
            if self._context_urls_snapshot(page) != baseline_urls:
                # URL mudou; pode ser a tela de turmas ou uma etapa intermediária.
                if await self._page_looks_like_turmas_abertas_anywhere(page):
                    return True
                return True
            await asyncio.sleep(0.15)
        return False

    async def _try_open_turmas_direct_routes(
        self,
        page: Page,
        *,
        token: CancelToken | None = None,
    ) -> Page | None:
        """Tenta abrir Turmas Abertas por endpoint direto do portal (mais estável que clique)."""
        self._check_cancel(token)
        if await self._page_looks_like_turmas_abertas_anywhere(page):
            return page

        current_url = page.url or ""
        if "sistemas2.utfpr.edu.br" not in current_url:
            return None

        for rel_path in selectors.TURMAS_ABERTAS_DIRECT_PATHS:
            self._check_cancel(token)
            target_url = urljoin(current_url, rel_path)
            logger.info("Tentando abrir Turmas Abertas por rota direta: %s", target_url)
            with contextlib.suppress(Exception):
                await page.goto(target_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                await asyncio.sleep(0.25)
                if await self._page_looks_like_turmas_abertas_anywhere(page):
                    logger.info("Turmas Abertas abertas por rota direta: %s", rel_path)
                    return page
                clicked_confirm = await self._maybe_click_confirm_anywhere(page, token=token)
                if clicked_confirm:
                    await asyncio.sleep(0.3)
                    if await self._page_looks_like_turmas_abertas_anywhere(page):
                        logger.info("Turmas Abertas abertas por rota direta + confirmar: %s", rel_path)
                        return page
        return None

    async def _click_portal_turmas_menu_js(self, page: Page, target_text: str) -> bool:
        script = """
        (menuSelector, targetText) => {
          const norm = (s) => (s || "").replace(/\\s+/g, " ").trim().toLowerCase();
          const target = norm(targetText);
          const root = document.querySelector(menuSelector) || document.body;
          const visible = (el) => {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            const st = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
          };
          const isClickable = (el) => {
            if (!el) return false;
            if (el.tagName === 'A' || el.tagName === 'BUTTON') return true;
            if (el.hasAttribute('onclick') || el.getAttribute('role') === 'button') return true;
            const cls = (el.className || '').toString().toLowerCase();
            return /button|menu|item|link/.test(cls);
          };
          const nodes = Array.from(root.querySelectorAll('*')).filter(visible);
          const fire = (node) => {
            for (const type of ['pointerdown','mousedown','mouseup','click']) {
              try { node.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window })); } catch (_) {}
            }
            try { node.click(); } catch (_) {}
          };
          for (const el of nodes) {
            const txt = norm(el.innerText || el.textContent || '');
            if (!txt || !txt.includes(target)) continue;
            const clickable = el.closest('a,button,[onclick],[role=\"button\"],div,td');
            const candidate = (clickable && visible(clickable) && isClickable(clickable)) ? clickable : el;
            if (visible(candidate)) {
              try { candidate.scrollIntoView({ block: 'center', inline: 'center' }); } catch (_) {}
              fire(candidate);
              return true;
            }
          }
          return false;
        }
        """
        with contextlib.suppress(Exception):
            clicked = await page.evaluate(script, selectors.PORTAL_MENU_CONTAINER_SELECTOR, target_text)
            if clicked:
                logger.info("Clique JS no menu '%s' executado", target_text)
                return True
        return False

    async def _click_turmas_in_iframes_js(self, page: Page, target_text: str) -> bool:
        """Tenta clicar em 'Turmas Abertas' dentro de iframes (ex.: if_navega/favoritos)."""
        script = """
        (targetText) => {
          const norm = (s) => (s || "").replace(/\\s+/g, " ").trim().toLowerCase();
          const target = norm(targetText);
          const visible = (el) => {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            const st = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
          };
          const isClickable = (el) => {
            if (!el) return false;
            if (el.tagName === "A" || el.tagName === "BUTTON") return true;
            if (el.hasAttribute("onclick") || el.getAttribute("role") === "button") return true;
            const cls = String(el.className || "").toLowerCase();
            return /button|btn|menu|item|link|card/.test(cls);
          };
          const nodes = Array.from(document.querySelectorAll("*")).filter(visible);
          const fire = (node) => {
            for (const type of ["pointerdown","mousedown","mouseup","click"]) {
              try { node.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window })); } catch (_) {}
            }
            try { node.click(); } catch (_) {}
          };
          for (const el of nodes) {
            const txt = norm(el.innerText || el.textContent || "");
            if (!txt || !txt.includes(target)) continue;
            const clickable = el.closest("a,button,[onclick],[role='button'],div,td,li");
            const candidate = clickable || el;
            if (!visible(candidate)) continue;
            try { candidate.scrollIntoView({ block: "center", inline: "center" }); } catch (_) {}
            fire(candidate);
            return true;
          }
          return false;
        }
        """
        for frame in page.frames:
            if frame is page.main_frame:
                continue
            with contextlib.suppress(Exception):
                clicked = bool(await frame.evaluate(script, target_text))
                if clicked:
                    logger.info("Clique JS em iframe no item '%s' executado", target_text)
                    return True
        return False

    async def _click_turmas_with_optional_popup(self, page: Page, *, token: CancelToken | None = None) -> Page:
        self._check_cancel(token)
        await self._prepare_portal_menu_if_needed(page, token=token)
        locators = []
        for css in selectors.TURMAS_ABERTAS_SELECTOR_HINTS:
            locators.append(("css", css))
        for text in selectors.TURMAS_ABERTAS_TEXTS:
            locators.append(("portal_menu_js", text))
            locators.append(("iframe_js", text))
            locators.append(("role_link", text))
            locators.append(("role_button", text))
            locators.append(("text", text))

        for kind, value in locators:
            try:
                baseline_urls = self._context_urls_snapshot(page)

                async def _do_click() -> None:
                    if kind == "css":
                        await page.locator(str(value)).first.click()
                    elif kind == "role_link":
                        await page.get_by_role(
                            "link", name=re.compile(re.escape(str(value)), re.IGNORECASE)
                        ).first.click()
                    elif kind == "role_button":
                        await page.get_by_role(
                            "button", name=re.compile(re.escape(str(value)), re.IGNORECASE)
                        ).first.click()
                    elif kind == "portal_menu_js":
                        if not await self._click_portal_turmas_menu_js(page, str(value)):
                            raise SelectorChangedError("Falha no clique JS do menu Turmas Abertas")
                    elif kind == "iframe_js":
                        if not await self._click_turmas_in_iframes_js(page, str(value)):
                            raise SelectorChangedError("Falha no clique JS em iframe para Turmas Abertas")
                    else:
                        await page.get_by_text(str(value), exact=False).first.click()

                popup: Page | None = None
                try:
                    async with page.expect_popup(timeout=selectors.POPUP_EXPECT_TIMEOUT_MS) as popup_info:
                        await _do_click()
                    popup = await popup_info.value
                except PlaywrightTimeoutError:
                    # Clique ocorreu na mesma aba.
                    popup = None

                target_page = popup or page
                with contextlib.suppress(Exception):
                    await target_page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
                if popup is not None:
                    # Popup abriu; valida se parece ser a página de turmas.
                    if await self._page_looks_like_turmas_abertas_anywhere(target_page):
                        return target_page
                    with contextlib.suppress(Exception):
                        await target_page.wait_for_timeout(300)
                    if await self._page_looks_like_turmas_abertas_anywhere(target_page):
                        return target_page
                    continue

                if await self._wait_turmas_open_after_click(
                    page,
                    baseline_urls=baseline_urls,
                    token=token,
                ):
                    return page
            except Exception:
                continue

        raise SelectorChangedError(
            "Nao foi possivel clicar em 'Turmas Abertas'. Ajuste src/infra/selectors.py."
        )

    async def _maybe_click_confirm(self, ctx: PageLike, *, token: CancelToken | None = None) -> bool:
        self._check_cancel(token)
        # Tenta primeiro por CSS.
        for css in selectors.CONFIRM_BUTTON_SELECTORS:
            try:
                locator = ctx.locator(css).first
                if await locator.count() == 0:
                    continue
                if not await locator.is_visible():
                    continue
                await locator.click()
                return True
            except Exception:
                continue
        # Fallback por texto no DOM.
        for text in selectors.CONFIRM_BUTTON_TEXTS:
            script = """
            (txt) => {
              const norm = (s) => (s || '').replace(/\\s+/g,' ').trim().toLowerCase();
              const target = norm(txt);
              const els = Array.from(document.querySelectorAll('button, a, input[type="submit"], input[type="button"]'));
              for (const el of els) {
                const content = norm(el.innerText || el.value || '');
                if (content.includes(target)) { el.click(); return true; }
              }
              return false;
            }
            """
            try:
                clicked = await ctx.evaluate(script, text)
                if clicked:
                    return True
            except Exception:
                continue
        return False

    async def _maybe_click_confirm_anywhere(self, page: Page, *, token: CancelToken | None = None) -> bool:
        self._check_cancel(token)
        if await self._maybe_click_confirm(page, token=token):
            return True
        for frame in page.frames:
            if frame is page.main_frame:
                continue
            self._check_cancel(token)
            with contextlib.suppress(Exception):
                if await self._maybe_click_confirm(frame, token=token):
                    return True
        return False

    async def _wait_table_anchor(
        self,
        ctx: PageLike,
        *,
        token: CancelToken | None = None,
        timeout_ms: int | None = None,
    ) -> None:
        self._check_cancel(token)
        effective_timeout = self.timeout_ms if timeout_ms is None else max(100, timeout_ms)
        await ctx.wait_for_selector(selectors.TURMAS_PAGE_TABLE_ANCHOR, timeout=effective_timeout)
        self._check_cancel(token)
        await ctx.wait_for_function(selectors.TURMAS_ROWS_FUNCTION, timeout=effective_timeout)

    async def _find_frame_with_table(
        self,
        page: Page,
        *,
        token: CancelToken | None = None,
        timeout_ms: int | None = None,
    ) -> PageLike | None:
        self._check_cancel(token)
        # Primeiro tenta a própria página.
        try:
            await self._wait_table_anchor(page, token=token, timeout_ms=timeout_ms)
            return page
        except Exception:
            pass
        # Depois varre iframes.
        for frame in page.frames:
            if frame is page.main_frame:
                continue
            self._check_cancel(token)
            try:
                await self._wait_table_anchor(frame, token=token, timeout_ms=timeout_ms)
                return frame
            except Exception:
                continue
        return None

    async def go_to_turmas_abertas(self, *, token: CancelToken | None = None) -> None:
        page = self._ensure_page()
        self._check_cancel(token)
        try:
            await self._set_flow_state(
                PortalFlowState.TURMAS_ABERTAS_ENTRY,
                step="go_to_turmas_abertas",
                detail="Iniciando abertura de Turmas Abertas",
                page=page,
            )
            target_page = await self._try_open_turmas_direct_routes(page, token=token)
            if target_page is None:
                target_page = await self._click_turmas_with_optional_popup(page, token=token)
            if not await self._page_looks_like_turmas_abertas_anywhere(target_page):
                retried_page = await self._try_open_turmas_direct_routes(target_page, token=token)
                if retried_page is not None:
                    target_page = retried_page

            # Se abriu popup, passa a usar a nova aba como pagina ativa.
            self.page = target_page
            self._check_cancel(token)
            with contextlib.suppress(Exception):
                await target_page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)

            # Aceita tanto tabela pronta quanto estado intermediario que exige curso.
            await self.ensure_turmas_table_ready(token=token)
        except CourseSelectionRequired:
            # Estado esperado: tela de Turmas Abertas aberta aguardando selecao do curso.
            raise
        except (PlaywrightError, PlaywrightTimeoutError, CancelledError, ScraperError, SelectorChangedError):
            await self._set_flow_state(
                PortalFlowState.FAILED,
                step="go_to_turmas_abertas_error",
                detail="Falha ao abrir Turmas Abertas",
                page=page,
            )
            await self._save_debug_artifacts("turmas_nav_error")
            raise

    # ---------- Extração rápida ----------
    async def _extract_tables_fast(self, ctx: PageLike) -> list[dict[str, Any]]:
        script = """
        () => {
          const clean = (s) => (s || "").replace(/\\s+/g, " ").trim();
          const norm = (s) => clean(s).toLowerCase();
          const collectContext = (table) => {
            const texts = [];
            if (table.caption) texts.push(clean(table.caption.innerText));
            let node = table.previousElementSibling;
            let hops = 0;
            while (node && hops < 6) {
              const txt = clean(node.innerText || node.textContent || "");
              if (txt) texts.push(txt);
              node = node.previousElementSibling;
              hops++;
            }
            return texts;
          };
          const tables = Array.from(document.querySelectorAll("table"));
          return tables.map((table, idx) => {
            let headers = Array.from(table.querySelectorAll("thead th")).map(th => clean(th.innerText));
            if (!headers.length) {
              const headRow = table.querySelector("tr");
              if (headRow) {
                headers = Array.from(headRow.children).map(el => clean(el.innerText));
              }
            }
            const rows = Array.from(table.querySelectorAll("tbody tr"))
              .map(tr => Array.from(tr.querySelectorAll("td")).map(td => clean(td.innerText)))
              .filter(row => row.some(Boolean));
            return {
              index: idx,
              headers,
              rows,
              context_texts: collectContext(table),
            };
          }).filter(t => t.rows.length > 0);
        }
        """
        return await ctx.evaluate(script)

    async def _extract_utfpr_turmas_rows_fast(self, ctx: PageLike) -> list[dict[str, Any]]:
        """Extrai linhas da tabela legacy de Turmas Abertas (UTFPR) em uma chamada JS.

        O HTML real do portal mistura:
        - linhas de titulo da disciplina (`td.t`)
        - linhas de cabecalho repetidas
        - linhas de dados (`td.sl/sc/sr/ml`)
        - colunas ocultas `td.dn`

        Este extrator remove `td.dn`, preserva o contexto da disciplina atual e retorna
        registros ja normalizados por posicao visivel da linha.
        """
        script = """
        () => {
          const clean = (s) => (s || "")
            .replace(/\\u00a0/g, " ")
            .replace(/\\s+/g, " ")
            .trim();

          const rootTable =
            document.querySelector("table[border='1']") ||
            Array.from(document.querySelectorAll("table")).find(
              (t) => t.querySelector("td.t") && t.querySelector("td.sl, td.sc, td.sr")
            );

          if (!rootTable) return [];

          let currentDisciplinaCodigo = "";
          let currentDisciplinaNome = "";
          const out = [];

          for (const tr of Array.from(rootTable.querySelectorAll("tr"))) {
            const titleCell = tr.querySelector("td.t");
            if (titleCell) {
              const titleNode = titleCell.querySelector("b") || titleCell;
              const rawTitle = clean(titleNode.innerText || titleNode.textContent || "");
              const m = rawTitle.match(/^([A-Z]{2,}\\d+[A-Z0-9]*)\\s*[-–]\\s*(.+)$/i);
              if (m) {
                currentDisciplinaCodigo = clean(m[1]).toUpperCase();
                currentDisciplinaNome = clean(m[2]);
              }
              continue;
            }

            const tds = Array.from(tr.querySelectorAll("td"));
            if (!tds.length) continue;

            const cells = tds
              .filter((td) => !td.classList.contains("dn"))
              .map((td) => ({
                text: clean(td.innerText || td.textContent || ""),
                cls: String(td.className || "").toLowerCase(),
              }));
            if (!cells.length) continue;

            const headerBlob = cells.map((c) => c.text.toLowerCase()).join(" | ");
            if (
              headerBlob.includes("horário (dia/turno/aula)") ||
              headerBlob.includes("horario (dia/turno/aula)")
            ) {
              continue;
            }

            const firstCls = cells[0].cls;
            if (
              !firstCls.includes("sl") &&
              !firstCls.includes("sc") &&
              !firstCls.includes("sr")
            ) {
              continue;
            }

            const get = (i) => clean((cells[i] && cells[i].text) || "");
            const turmaCodigo = get(0);
            if (!turmaCodigo || !/^[A-Za-z0-9]+$/.test(turmaCodigo)) continue;

            out.push({
              disciplina_codigo: currentDisciplinaCodigo,
              disciplina_nome: currentDisciplinaNome,
              turma_codigo: turmaCodigo,
              enquadramento: get(1),
              vagas_total: get(2),
              vagas_calouros: get(3),
              reserva: get(4),
              prioridade: get(5),
              horario_raw: get(6),
              professor: get(7),
              optativa: get(8),
            });
          }

          return out;
        }
        """
        return await ctx.evaluate(script)

    async def _extract_utfpr_turmas_rows_from_html_source(self, ctx: PageLike) -> list[dict[str, Any]]:
        html_text = await self._ctx_content_html(ctx)
        if not html_text:
            return []

        root_match = re.search(
            r"(?is)<table\b[^>]*border\s*=\s*['\"]?1['\"]?[^>]*>(?P<body>.*?)</table>",
            html_text,
        )
        table_html = root_match.group("body") if root_match else html_text
        rows: list[dict[str, Any]] = []
        current_disc_codigo = ""
        current_disc_nome = ""

        for tr_match in re.finditer(r"(?is)<tr\b[^>]*>(?P<body>.*?)</tr>", table_html):
            tr_html = tr_match.group("body")

            title_match = re.search(
                r"(?is)<td\b[^>]*class\s*=\s*['\"][^'\"]*\bt\b[^'\"]*['\"][^>]*>(?P<td>.*?)</td>",
                tr_html,
            )
            if title_match:
                title_html = title_match.group("td")
                b_match = re.search(r"(?is)<b\b[^>]*>(?P<t>.*?)</b>", title_html)
                raw_title = self._html_text((b_match.group("t") if b_match else title_html) or "")
                m = re.match(r"^([A-Z]{2,}\d+[A-Z0-9]*)\s*[-–]\s*(.+)$", raw_title, flags=re.I)
                if m:
                    current_disc_codigo = m.group(1).strip().upper()
                    current_disc_nome = m.group(2).strip()
                continue

            visible_cells: list[tuple[str, str]] = []
            for td_match in re.finditer(r"(?is)<td\b(?P<attrs>[^>]*)>(?P<td>.*?)</td>", tr_html):
                attrs = td_match.group("attrs") or ""
                cls_match = re.search(r'(?is)\bclass\s*=\s*["\']([^"\']*)', attrs)
                cls = (cls_match.group(1) if cls_match else "").lower()
                if " dn" in f" {cls} ":
                    continue
                visible_cells.append((self._html_text(td_match.group("td") or ""), cls))

            if not visible_cells:
                continue

            header_blob = " | ".join(cell for cell, _ in visible_cells).lower()
            if "horario (dia/turno/aula)" in header_blob or "horário (dia/turno/aula)" in header_blob:
                continue

            turma_codigo = visible_cells[0][0].strip()
            if not turma_codigo or len(visible_cells) < 7:
                continue

            def _get(idx: int) -> str:
                return visible_cells[idx][0].strip() if idx < len(visible_cells) else ""

            rows.append(
                {
                    "disciplina_codigo": current_disc_codigo,
                    "disciplina_nome": current_disc_nome,
                    "turma_codigo": turma_codigo,
                    "enquadramento": _get(1),
                    "vagas_total": _get(2),
                    "vagas_calouros": _get(3),
                    "reserva": _get(4),
                    "prioridade": _get(5),
                    "horario_raw": _get(6),
                    "professor": _get(7),
                    "optativa": _get(8),
                }
            )

        if rows:
            logger.info("Tabela UTFPR legacy extraida via codigo-fonte HTML (%d linhas)", len(rows))
        return rows

    async def _header_value(self, page: Page) -> tuple[str | None, str | None]:
        for css in selectors.DISCIPLINA_HEADER_SELECTORS:
            try:
                text = ((await page.locator(css).first.text_content()) or "").strip()
            except Exception:
                continue
            if not text:
                continue
            match = selectors.DISCIPLINA_HEADER_RE.search(text)
            if match:
                return (match.group("codigo").strip().upper(), match.group("nome").strip())
        return (None, None)

    @staticmethod
    def _norm(text: str) -> str:
        text = unicodedata.normalize("NFKD", (text or ""))
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        return " ".join(text.lower().split())

    @classmethod
    def _column_index_map(cls, headers: list[str]) -> dict[str, int]:
        normalized = [cls._norm(h) for h in headers]
        mapping: dict[str, int] = {}
        for field, hints in selectors.COLUMN_HINTS.items():
            for idx, head in enumerate(normalized):
                if any(cls._norm(hint) in head for hint in hints):
                    mapping[field] = idx
                    break
        return mapping

    @staticmethod
    def _to_int(value: str | None) -> int | None:
        if not value:
            return None
        digits = "".join(ch for ch in value if ch.isdigit())
        return int(digits) if digits else None

    def _infer_disciplina_from_context(
        self,
        context_texts: list[str],
        page_disciplina_codigo: str | None,
        page_disciplina_nome: str | None,
    ) -> tuple[str, str]:
        for txt in context_texts:
            match = selectors.DISCIPLINA_HEADER_RE.search(txt)
            if match:
                return (match.group("codigo").strip().upper(), match.group("nome").strip())
        return (page_disciplina_codigo or "", page_disciplina_nome or "")

    def _rows_to_turmas(
        self,
        *,
        headers: list[str],
        rows: list[list[str]],
        context_texts: list[str],
        page_disciplina_codigo: str | None,
        page_disciplina_nome: str | None,
    ) -> list[Turma]:
        mapping = self._column_index_map(headers)
        if "turma_codigo" not in mapping or "horario_raw" not in mapping:
            return []

        disc_codigo, disc_nome = self._infer_disciplina_from_context(
            context_texts,
            page_disciplina_codigo,
            page_disciplina_nome,
        )
        turmas: list[Turma] = []
        for row in rows:
            try:
                turma_codigo = row[mapping["turma_codigo"]].strip()
                horario_raw = row[mapping["horario_raw"]].strip()
            except Exception:
                continue
            if not turma_codigo or not horario_raw:
                continue

            try:
                horarios = parse_horarios(horario_raw)
            except ValueError:
                logger.warning("Horario invalido ignorado: %s | turma=%s", horario_raw, turma_codigo)
                continue

            def _get(field: str) -> str | None:
                idx = mapping.get(field)
                if idx is None or idx >= len(row):
                    return None
                value = row[idx].strip()
                return value or None

            turmas.append(
                Turma(
                    disciplina_codigo=disc_codigo,
                    disciplina_nome=disc_nome,
                    turma_codigo=turma_codigo,
                    horario_raw=horario_raw,
                    horarios=horarios,
                    professor=_get("professor"),
                    vagas_total=self._to_int(_get("vagas_total")),
                    vagas_calouros=self._to_int(_get("vagas_calouros")),
                    status=_get("status"),
                    prioridade=_get("prioridade"),
                )
            )
        return turmas

    def _utfpr_table_rows_to_turmas(self, rows: list[dict[str, Any]]) -> list[Turma]:
        """Converte linhas extraidas do markup legacy da UTFPR em `Turma`."""

        def _clean(value: object) -> str | None:
            text = str(value or "").strip()
            return text or None

        turmas: list[Turma] = []
        for row in rows:
            turma_codigo = str(row.get("turma_codigo", "")).strip()
            if not turma_codigo:
                continue

            horario_raw = str(row.get("horario_raw", "")).strip()
            horarios = []
            if horario_raw:
                try:
                    horarios = parse_horarios(horario_raw)
                except ValueError:
                    logger.warning(
                        "Horario invalido ignorado (utfpr legacy): %s | turma=%s",
                        horario_raw,
                        turma_codigo,
                    )
                    continue

            turmas.append(
                Turma(
                    disciplina_codigo=str(row.get("disciplina_codigo", "")).strip(),
                    disciplina_nome=str(row.get("disciplina_nome", "")).strip(),
                    turma_codigo=turma_codigo,
                    horario_raw=horario_raw,
                    horarios=horarios,
                    professor=_clean(row.get("professor")),
                    vagas_total=self._to_int(_clean(row.get("vagas_total"))),
                    vagas_calouros=self._to_int(_clean(row.get("vagas_calouros"))),
                    status=_clean(row.get("reserva")),
                    prioridade=_clean(row.get("prioridade")),
                )
            )
        return turmas

    async def _click_next_page(self, ctx: PageLike, *, token: CancelToken | None = None) -> bool:
        self._check_cancel(token)
        for css in selectors.PAGINATION_NEXT_SELECTORS:
            try:
                locator = ctx.locator(css).first
                if await locator.count() == 0:
                    continue
                if not await locator.is_visible():
                    continue
                cls = ((await locator.get_attribute("class")) or "").lower()
                aria_disabled = ((await locator.get_attribute("aria-disabled")) or "").lower()
                if "disabled" in cls or aria_disabled in {"true", "1"}:
                    continue
                await locator.click()
                await asyncio.sleep(0.2)
                await self._wait_table_anchor(ctx, token=token)
                return True
            except Exception:
                continue
        return False

    async def fetch_turmas_abertas(
        self,
        *,
        token: CancelToken | None = None,
        max_pages: int = 50,
    ) -> list[Turma]:
        page = self._ensure_page()
        ctx = self._active_table_context or page
        self._check_cancel(token)

        all_turmas: dict[str, Turma] = {}
        visited_signatures: set[str] = set()
        page_disciplina_codigo, page_disciplina_nome = await self._header_value(page)

        for page_num in range(1, max_pages + 1):
            self._check_cancel(token)

            utfpr_rows = await self._extract_utfpr_turmas_rows_fast(ctx)
            if not utfpr_rows:
                utfpr_rows = await self._extract_utfpr_turmas_rows_from_html_source(ctx)
            tables: list[dict[str, Any]] = []
            if utfpr_rows:
                signature = (
                    f"{getattr(ctx, 'url', page.url)}|utfpr|{len(utfpr_rows)}|"
                    f"{utfpr_rows[0].get('disciplina_codigo', '')}|"
                    f"{utfpr_rows[-1].get('turma_codigo', '')}"
                )
            else:
                tables = await self._extract_tables_fast(ctx)
                if not tables:
                    await self._save_debug_artifacts("turmas_sem_tabela")
                    raise SelectorChangedError(
                        "Nenhuma tabela com linhas encontrada em Turmas Abertas. "
                        "Ajuste src/infra/selectors.py."
                    )
                signature = (
                    f"{getattr(ctx, 'url', page.url)}|"
                    f"{sum(len(t.get('rows', [])) for t in tables)}"
                )

            if signature in visited_signatures:
                logger.info("Pagina repetida detectada; encerrando iteracao")
                break
            visited_signatures.add(signature)

            added_this_page = 0
            if utfpr_rows:
                logger.info(
                    "Pagina %d: extrator UTFPR especifico encontrou %d linhas",
                    page_num,
                    len(utfpr_rows),
                )
                turmas_pagina = self._utfpr_table_rows_to_turmas(utfpr_rows)
                for turma in turmas_pagina:
                    all_turmas[turma.uid()] = turma
                    added_this_page += 1
            else:
                for table in tables:
                    headers = [str(h) for h in table.get("headers", [])]
                    rows = [
                        [str(c) for c in row]
                        for row in table.get("rows", [])
                        if isinstance(row, list)
                    ]
                    context_texts = [
                        str(t) for t in table.get("context_texts", []) if isinstance(t, str)
                    ]
                    turmas = self._rows_to_turmas(
                        headers=headers,
                        rows=rows,
                        context_texts=context_texts,
                        page_disciplina_codigo=page_disciplina_codigo,
                        page_disciplina_nome=page_disciplina_nome,
                    )
                    for turma in turmas:
                        all_turmas[turma.uid()] = turma
                        added_this_page += 1
            logger.info("Pagina %d processada: %d turmas", page_num, added_this_page)

            self._check_cancel(token)
            if not await self._click_next_page(ctx, token=token):
                break
            # Após paginar, pode trocar o frame. Re-resolve para robustez.
            ctx = (await self._find_frame_with_table(page, token=token)) or page
            self._active_table_context = ctx
        else:
            logger.warning("Paginacao interrompida apos %d paginas (limite de seguranca)", max_pages)

        if not all_turmas:
            await self._save_debug_artifacts("turmas_parse_vazio")
            raise SelectorChangedError(
                "Tabela encontrada, mas nao foi possivel mapear colunas de turma/horario. "
                "Ajuste COLUMN_HINTS em src/infra/selectors.py."
            )

        await self._persist_storage_state()
        return sorted(all_turmas.values(), key=lambda t: (t.disciplina_codigo, t.disciplina_nome, t.turma_codigo))

