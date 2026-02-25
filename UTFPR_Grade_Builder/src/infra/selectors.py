from __future__ import annotations

import re

PORTAL_PUBLIC_ALUNO_URL = "https://www.utfpr.edu.br/alunos/portal-do-aluno"
PORTAL_ENTRY_URL = "https://sistemas2.utfpr.edu.br/"
LOGIN_URL = "https://sistemas2.utfpr.edu.br/login"

# Login (portal UTFPR pode variar; manter pontos de ajuste fáceis)
SELECTOR_USERNAME = 'input[name="username"], input[id*="user" i], input[type="text"]'
SELECTOR_PASSWORD = 'input[name="password"], input[type="password"]'
SELECTOR_LOGIN_BUTTON = (
    'button[type="submit"], input[type="submit"], '
    'button:has-text("Login"), button:has-text("Entrar"), a:has-text("Login")'
)

USERNAME_LABELS = ("Usuario", "Usuário", "RA", "Matricula", "Matrícula")
PASSWORD_LABELS = ("Senha", "Password")
LOGIN_BUTTON_TEXTS = ("Login", "Entrar", "Acessar")

MANUAL_STEP_KEYWORDS = (
    "captcha",
    "2fa",
    "token",
    "codigo",
    "código",
    "autenticacao",
    "autenticação",
)

# Seleção de campus antes do login (página inicial do sistemas2)
DEFAULT_CAMPUS_NAME = "Curitiba"
PORTAL_HOME_SHELL_KEYWORDS = (
    "sistemas corporativos integrados utfpr",
    "sistemas corporativos integrados da utfpr",
)
PORTAL_HOME_TAB_TEXT = "Portal do Aluno"
CAMPUS_PAGE_CITY_KEYWORDS = (
    "Apucarana",
    "Campo Mourão",
    "Cornélio Procópio",
    "Curitiba",
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

# Menu / navegação
TURMAS_ABERTAS_TEXTS = ("Turmas Abertas",)
TURMAS_ABERTAS_SELECTOR_HINTS = (
    'a:has-text("Turmas Abertas")',
    'button:has-text("Turmas Abertas")',
)
PORTAL_ALUNO_KEYWORDS = ("seja bem-vindo ao portal do aluno",)
PORTAL_IFRAME_SELECTOR = "iframe#if_navega"
PORTAL_MENU_CONTAINER_SELECTOR = "#div_CarregaAjaxMenu"
PORTAL_TURMAS_PAGE_KEYWORDS = (
    "relação de turmas abertas para a matrícula",
    "relacao de turmas abertas para a matricula",
)
TURMAS_ABERTAS_DIRECT_PATHS = (
    "mplistahorario.inicio",
    "mplistahorario.psInicio",
)
POPUP_EXPECT_TIMEOUT_MS = 1500

# Página de Turmas Abertas (podem existir filtros antes da tabela)
TURMAS_PAGE_TABLE_ANCHOR = "table"
TURMAS_ROWS_FUNCTION = (
    "() => !!document.querySelector('table td.t') || "
    "Array.from(document.querySelectorAll('table tbody tr, table tr')).some("
    "tr => tr.querySelectorAll('td').length > 0)"
)
UTFPR_TURMAS_MAIN_TABLE_SELECTOR = "table[border='1']"
UTFPR_TURMAS_TITLE_CELL_CLASS = "t"
UTFPR_TURMAS_HIDDEN_CELL_CLASS = "dn"
CONFIRM_BUTTON_TEXTS = ("Confirmar>>", "Confirmar", "Pesquisar")
CONFIRM_BUTTON_SELECTORS = (
    'button:has-text("Confirmar")',
    'input[type="submit"][value*="Confirmar" i]',
    'a:has-text("Confirmar")',
)

IFRAME_TABLE_HINTS = (
    "table",
    "tbody tr",
)

TABLE_CANDIDATE_SELECTORS = ("table", ".table", "[role='table']")
PAGINATION_NEXT_SELECTORS = (
    "a[aria-label*='next' i]",
    "a[title*='Proxima' i]",
    "a[title*='Próxima' i]",
    ".pagination a:has-text('>')",
    ".pagination a:has-text('Próxima')",
)

DISCIPLINA_HEADER_SELECTORS = ("h1", "h2", ".titulo", ".page-title", ".panel-title", "b")
DISCIPLINA_HEADER_RE = re.compile(
    r"(?P<codigo>[A-Z]{2,}\d+[A-Z0-9]*)\s*[-–]\s*(?P<nome>.+)",
    re.IGNORECASE,
)

COLUMN_HINTS: dict[str, tuple[str, ...]] = {
    "turma_codigo": ("turma",),
    "horario_raw": ("horario", "horário"),
    "professor": ("professor",),
    "vagas_total": ("vagas total", "vagas"),
    "vagas_calouros": ("calouros",),
    "status": ("status", "reserva", "situacao", "situação"),
    "prioridade": ("prioridade",),
}

# Timeouts / retry
DEFAULT_TIMEOUT_MS = 8000
STEP_RETRIES = 2
RETRY_BACKOFF_MS = 400
