<div align="center">

# <span style="background: linear-gradient(90deg, #0ea5e9 0%, #2563eb 50%, #1d4ed8 100%); -webkit-background-clip: text; color: transparent;">Grade Na Hora UTFPR</span>

<p align="center">
  <b>Monte sua grade da UTFPR com scraping inteligente, detecção de conflitos e exportação em PNG.</b>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-1d4ed8?style=for-the-badge&logo=python&logoColor=white">
  <img alt="PySide6" src="https://img.shields.io/badge/PySide6-Desktop_UI-0ea5e9?style=for-the-badge&logo=qt&logoColor=white">
  <img alt="Playwright" src="https://img.shields.io/badge/Playwright-Scraping-2563eb?style=for-the-badge&logo=playwright&logoColor=white">
  <img alt="Pillow" src="https://img.shields.io/badge/Pillow-PNG_Export-1e40af?style=for-the-badge">
</p>

<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=rect&height=8&color=0:0ea5e9,50:2563eb,100:1d4ed8&section=header" width="100%" />
</p>

</div>

## Visão Geral

Aplicativo desktop em Python (`PySide6` + `Playwright`) para:
- acessar o **Portal do Aluno UTFPR**,
- abrir **Turmas Abertas**,
- selecionar **campus** e **curso**,
- extrair horários das turmas,
- montar grade semanal,
- detectar conflitos,
- exportar a grade em **PNG** (sem screenshot da UI).

## Fluxo Suportado (UTFPR)

O app foi ajustado para o fluxo real do portal:

1. `https://www.utfpr.edu.br/alunos/portal-do-aluno`
2. Seleção de **cidade/campus** (ex.: `Curitiba`)
3. **Login**
4. Entrada no **Portal do Aluno**
5. Clique em **Turmas Abertas**
6. Se necessário, **seleção dinâmica de curso** (dropdown da página)
7. Extração das turmas/horários e montagem da grade

## Recursos Principais

- Login no Portal do Aluno UTFPR (RA + senha, com prefixo `a` opcional)
- Seleção de **campus** na tela inicial do app
- Seleção **dinâmica de curso** quando a página `Turmas Abertas` exigir
- Scraping em background (`QThread` + worker) sem travar a UI
- Navegação robusta com suporte a `iframe`, popup e rotas alternativas
- Modo debug (browser visível + logs + screenshots/HTML em erro)
- Parser de horários UTFPR (`2M1`, `5T2(CE-208)`, `6N1-2`, `*EK-307`, etc.)
- Lista de turmas com seleção, filtro e cálculo de créditos
- Grade semanal (Seg..Sáb x M1..N5) com conflitos destacados
- Relatório de seleção
- Exportação PNG em alta resolução com `Pillow`

## Stack

- `Python 3.11+`
- `PySide6` (interface desktop)
- `Playwright` (automação/scraping)
- `Pillow` (exportação PNG)
- `python-dotenv` (config opcional)
- `pytest` / `ruff` (testes e qualidade)

## Instalação (Windows / PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
```

## Como Executar

```powershell
python -m src.app
```

## Smoke Test (abre e fecha automático)

```powershell
python -m src.app --smoke-ms 1500
```

## Como Usar (fluxo recomendado)

1. Abra o app.
2. Escolha o **campus/cidade**.
3. Informe **RA** e **senha**.
4. (Opcional) marque `Modo debug (browser visível)`.
5. Clique em `ENTRAR`.
6. Se o portal pedir, selecione o **curso** no combo exibido pelo app.
7. Aguarde a carga das turmas.
8. Selecione as turmas e gere sua grade.
9. Exporte em PNG se quiser salvar/compartilhar.

## Modo Debug (quando algo falhar)

Se algum seletor ou navegação do portal mudar:

- Ative `Modo debug (browser visível)`
- Reproduza o fluxo
- Verifique os artefatos:
  - `logs/app.log`
  - `logs/screenshots/*.png`
  - `logs/html/*.html`
- Ajuste os seletores em `src/infra/selectors.py`

## Segurança

- Senha fica **somente em memória**
- Persistência local (sem senha):
  - `data/app_state.json` (preferências/seleções)
  - `data/turmas_cache.json` (cache de turmas, quando usado)
  - `data/storageState.json` (sessão/cookies Playwright, opcional)

## Estrutura do Projeto

```text
UTFPR_Grade_Builder/
├─ src/
│  ├─ app.py
│  ├─ core/
│  ├─ infra/
│  └─ ui/
├─ data/
├─ logs/
├─ assets/
├─ tests/
├─ requirements.txt
└─ README.md
```

## Testes

```powershell
pytest -q
```

## Lint

```powershell
ruff check src tests
```

## Observações

- A interface e o fluxo foram pensados para o **Portal do Aluno UTFPR**, que pode variar por campus/versão.
- Alguns passos (captcha/2FA, mudanças de layout) podem exigir intervenção manual.
- O parser já trata vários formatos reais de horários da UTFPR, incluindo marcações com `*`.

<div align="center">
  <img src="https://capsule-render.vercel.app/api?type=rect&height=8&color=0:1d4ed8,50:2563eb,100:0ea5e9&section=footer" width="100%" />
</div>
