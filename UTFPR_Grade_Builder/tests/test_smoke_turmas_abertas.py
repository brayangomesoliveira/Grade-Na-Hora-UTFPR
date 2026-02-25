from __future__ import annotations

import asyncio
import os

import pytest

from src.infra.cancel_token import CancelToken
from src.infra.scraper_async import CourseSelectionRequired, UtfprScraperAsync


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _build_username() -> str:
    matricula = (os.getenv("UTFPR_MATRICULA") or "").strip()
    if not matricula:
        return ""
    add_prefix = _bool_env("UTFPR_PREFIX_A", True)
    if add_prefix and not matricula.lower().startswith("a"):
        return f"a{matricula}"
    return matricula


@pytest.mark.skipif(
    os.getenv("UTFPR_SMOKE_REAL") not in {"1", "true", "TRUE"},
    reason="Smoke real desabilitado. Use UTFPR_SMOKE_REAL=1 com credenciais de teste.",
)
def test_smoke_turmas_abertas_flow(browser_name: str) -> None:
    """Smoke real do fluxo até Turmas Abertas.

    Usa o scraper assíncrono do projeto. Se o portal exigir captcha/2FA, marca xfail.
    Se abrir tela intermediária de curso, isso já conta como sucesso do fluxo de navegação.
    """
    _ = browser_name  # fixture do pytest-playwright (garante plugin ativo)
    username = _build_username()
    password = (os.getenv("UTFPR_SENHA") or "").strip()
    campus = (os.getenv("UTFPR_CAMPUS") or "Curitiba").strip()

    if not username or not password:
        pytest.skip("Defina UTFPR_MATRICULA e UTFPR_SENHA para rodar o smoke real.")

    async def _run() -> None:
        token = CancelToken()
        scraper = UtfprScraperAsync(
            headless=_bool_env("UTFPR_HEADLESS", True),
            timeout_ms=int(os.getenv("UTFPR_TIMEOUT_MS", "15000")),
            retries=2,
            default_campus_name=campus,
        )
        await scraper.start()
        scraper.bind_runtime(loop=asyncio.get_running_loop(), cancel_token=token)
        try:
            login_result = await scraper.login(username, password, token=token)
            if login_result.manual_step_required:
                pytest.xfail("CAPTCHA/2FA detectado no smoke real.")
            assert login_result.ok, login_result.message

            try:
                await scraper.go_to_turmas_abertas(token=token)
            except CourseSelectionRequired as exc:
                assert exc.options, "Curso exigido, mas nenhuma opcao foi extraida."
                return

            # Se não exigiu curso, a tabela de turmas precisa estar acessível.
            assert scraper.page is not None
            assert scraper._active_table_context is not None, "Tabela de Turmas Abertas nao foi localizada."
        finally:
            await scraper.close()

    asyncio.run(_run())
