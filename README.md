# Grade Na Hora UTFPR (Python)

App desktop para montar grade horaria com `customtkinter`, automacao com `playwright` e exportacao PNG com `Pillow`.

## Instalacao

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .[dev]
playwright install chromium
```

## Execucao (Etapa 1)

Nesta etapa estao disponiveis apenas o dominio e o parser de horarios (sem UI/scraper real ainda).

## Testes

```powershell
pytest -q -p no:cacheprovider
```

## Persistencia e seguranca (planejado)

- Estado local em JSON (`app_state.json`)
- Sessao Playwright opcional em `storageState.json`
- Senha nunca e salva em disco