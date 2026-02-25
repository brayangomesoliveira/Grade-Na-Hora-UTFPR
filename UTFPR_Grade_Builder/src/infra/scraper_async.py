from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

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
    ) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.storage_state_path = Path(storage_state_path) if storage_state_path else None
        self.retries = max(0, retries)

        self._pw = None
        self._browser = None
        self._context = None
        self.page: Page | None = None
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._cancel_token: CancelToken | None = None
        self._active_table_context: PageLike | None = None

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
        logger.info("Playwright async iniciado (headless=%s)", self.headless)

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
        if token is not None:
            token.raise_if_cancelled()

    def _ensure_page(self) -> Page:
        if self.page is None:
            raise ScraperError("Pagina Playwright nao iniciada.")
        return self.page

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
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            self._check_cancel(token)
            try:
                return await coro_factory()
            except (PlaywrightTimeoutError, PlaywrightError, SelectorChangedError, ScraperError) as exc:
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
        await page.goto(selectors.LOGIN_URL, wait_until="domcontentloaded", timeout=self.timeout_ms)

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
            await asyncio.sleep(0.15)
            if await self._manual_step_detected(page):
                return LoginResult(
                    ok=False,
                    manual_step_required=True,
                    message="Conclua manualmente e clique em Continuar (captcha/2FA).",
                )
            await self._persist_storage_state()
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
            return LoginResult(
                ok=False,
                manual_step_required=True,
                message="Ainda ha indicios de captcha/2FA. Finalize no navegador e clique Continuar novamente.",
            )
        await self._persist_storage_state()
        return LoginResult(ok=True, message="Etapa manual concluida.")

    # ---------- Navegação para Turmas Abertas ----------
    async def _click_turmas_with_optional_popup(self, page: Page, *, token: CancelToken | None = None) -> Page:
        self._check_cancel(token)
        locators = []
        for css in selectors.TURMAS_ABERTAS_SELECTOR_HINTS:
            locators.append(("css", css))
        for text in selectors.TURMAS_ABERTAS_TEXTS:
            locators.append(("role_link", text))
            locators.append(("role_button", text))
            locators.append(("text", text))

        for kind, value in locators:
            try:
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
                return target_page
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

    async def _wait_table_anchor(self, ctx: PageLike, *, token: CancelToken | None = None) -> None:
        self._check_cancel(token)
        await ctx.wait_for_selector(selectors.TURMAS_PAGE_TABLE_ANCHOR, timeout=self.timeout_ms)
        self._check_cancel(token)
        await ctx.wait_for_function(selectors.TURMAS_ROWS_FUNCTION, timeout=self.timeout_ms)

    async def _find_frame_with_table(self, page: Page, *, token: CancelToken | None = None) -> PageLike | None:
        self._check_cancel(token)
        # Primeiro tenta a própria página.
        try:
            await self._wait_table_anchor(page, token=token)
            return page
        except Exception:
            pass
        # Depois varre iframes.
        for frame in page.frames:
            if frame is page.main_frame:
                continue
            self._check_cancel(token)
            try:
                await self._wait_table_anchor(frame, token=token)
                return frame
            except Exception:
                continue
        return None

    async def go_to_turmas_abertas(self, *, token: CancelToken | None = None) -> None:
        page = self._ensure_page()
        self._check_cancel(token)
        try:
            target_page = await self._click_turmas_with_optional_popup(page, token=token)
            # Se abriu popup, passa a usar a nova aba como página ativa.
            self.page = target_page
            self._check_cancel(token)
            # Tenta confirmar filtros, se a página exigir.
            with contextlib.suppress(Exception):
                await target_page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
            # Busca tabela direto; se ainda não houver linhas, tenta "Confirmar".
            ctx = await self._find_frame_with_table(target_page, token=token)
            if ctx is None:
                with contextlib.suppress(Exception):
                    clicked = await self._maybe_click_confirm(target_page, token=token)
                    if clicked:
                        await asyncio.sleep(0.2)
                ctx = await self._find_frame_with_table(target_page, token=token)
            if ctx is None:
                await self._save_debug_artifacts("turmas_anchor_error")
                raise SelectorChangedError(
                    "Nao foi possivel localizar a tabela de Turmas Abertas (page/iframe). "
                    "Ajuste src/infra/selectors.py."
                )
            self._active_table_context = ctx
        except (PlaywrightError, PlaywrightTimeoutError, CancelledError, ScraperError, SelectorChangedError):
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
        text = (text or "").lower()
        table = str.maketrans("áàãâéêíóôõúç", "aaaaeeiooouc")
        return " ".join(text.translate(table).split())

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
        page_num = 0
        page_disciplina_codigo, page_disciplina_nome = await self._header_value(page)

        while True:
            self._check_cancel(token)
            page_num += 1
            if page_num > max_pages:
                logger.warning("Paginacao interrompida apos %d paginas (limite de seguranca)", max_pages)
                break

            utfpr_rows = await self._extract_utfpr_turmas_rows_fast(ctx)
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

        if not all_turmas:
            await self._save_debug_artifacts("turmas_parse_vazio")
            raise SelectorChangedError(
                "Tabela encontrada, mas nao foi possivel mapear colunas de turma/horario. "
                "Ajuste COLUMN_HINTS em src/infra/selectors.py."
            )

        await self._persist_storage_state()
        return sorted(all_turmas.values(), key=lambda t: (t.disciplina_codigo, t.disciplina_nome, t.turma_codigo))
