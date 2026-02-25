# UTFPR_Grade_Builder

Aplicativo desktop em Python (`PySide6` + `Playwright`) para montar grade horária da UTFPR, detectar conflitos e exportar PNG via `Pillow` (sem screenshot).

## Recursos

- Login no Portal do Aluno UTFPR (RA + senha, com prefixo `a` opcional)
- Scraping de “Turmas Abertas” em background (`QThread` + Worker) sem travar a UI
- Cancelamento imediato (fecha contexto/browser sem logout por menu)
- Estratégia robusta para popup/iframe/tabela carregada
- Modo debug (browser visível + logs + screenshot/html automáticos em erro)
- Cache local JSON para testar UI sem scraping
- Lista de turmas com checkbox, filtro, créditos/limite
- Grade semanal (Seg..Sáb x M1..N5) com conflitos destacados
- Relatório e exportação PNG em alta resolução

## Estrutura

```text
UTFPR_Grade_Builder/
  src/
    app.py
    ui/
    core/
    infra/
  assets/
  logs/
  data/
  tests/
```

## Requisitos

- Python 3.11+
- `PySide6`
- `Playwright` + Chromium (`playwright install chromium`)

## Instalação (Windows / PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
```

## Como rodar

```powershell
python -m src.app
```

## Smoke test (abre e fecha automaticamente)

```powershell
python -m src.app --smoke-ms 1500
```

## Modo debug / ajuste de seletores

1. Marque `Modo debug (browser visível)` na tela de login.
2. Faça login e clique em `ATUALIZAR`.
3. Se algum seletor falhar, o app salva:
   - `logs/screenshots/*.png`
   - `logs/html/*.html`
   - `logs/app.log`
4. Ajuste `src/infra/selectors.py`.

## Cache JSON (sem scraping)

- Use `Carregar cache JSON` (login) ou `Carregar cache`/`ATUALIZAR` com cache salvo.
- O formato é gerado por `src/core/storage.py` (`save_turmas_cache`).
- A senha nunca é armazenada.

## Segurança

- Senha somente em memória (não salva em arquivo)
- Persistência local:
  - `data/app_state.json` (tema, limite, flags, seleções)
  - `data/turmas_cache.json` (cache de turmas)
  - `data/storageState.json` (cookies/sessão Playwright, opcional)

## Testes

```powershell
pytest -q
```

## Lint

```powershell
ruff check src tests
```
