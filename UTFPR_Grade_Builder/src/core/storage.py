from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .models import Turma
from .schedule import parse_horarios
from .state import AppState

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
DEFAULT_CACHE_PATH = ROOT_DIR / os.getenv("UTFPR_CACHE_PATH", "data/turmas_cache.json")
DEFAULT_APP_STATE_PATH = ROOT_DIR / os.getenv("UTFPR_APP_STATE_PATH", "data/app_state.json")
DEFAULT_STORAGE_STATE_PATH = ROOT_DIR / os.getenv("UTFPR_STORAGE_STATE_PATH", "data/storageState.json")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_APP_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_STORAGE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)


def save_json(path: str | Path, data: object) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_json(path: str | Path) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_turmas_cache(turmas: list[Turma], path: str | Path | None = None) -> Path:
    ensure_dirs()
    target = Path(path) if path else DEFAULT_CACHE_PATH
    logger.info("Salvando cache de turmas em %s (%d itens)", target, len(turmas))
    return save_json(target, [t.to_dict() for t in turmas])


def _normalize_loaded_turma(data: dict) -> Turma:
    turma = Turma.from_dict(data)
    if not turma.horarios and turma.horario_raw:
        turma.horarios = parse_horarios(turma.horario_raw)
    return turma


def load_turmas_cache(path: str | Path | None = None) -> list[Turma]:
    target = Path(path) if path else DEFAULT_CACHE_PATH
    if not target.exists():
        raise FileNotFoundError(f"Arquivo de cache nao encontrado: {target}")
    raw = load_json(target)
    if not isinstance(raw, list):
        raise ValueError("Cache JSON invalido: esperado lista de turmas")
    turmas = [_normalize_loaded_turma(item) for item in raw if isinstance(item, dict)]
    logger.info("Cache carregado de %s (%d turmas)", target, len(turmas))
    return turmas


def save_app_state(state: AppState, path: str | Path | None = None) -> Path:
    ensure_dirs()
    return save_json(Path(path) if path else DEFAULT_APP_STATE_PATH, state.to_dict())


def load_app_state(path: str | Path | None = None) -> AppState:
    target = Path(path) if path else DEFAULT_APP_STATE_PATH
    if not target.exists():
        return AppState()
    raw = load_json(target)
    if not isinstance(raw, dict):
        logger.warning("app_state invalido; usando defaults")
        return AppState()
    return AppState.from_dict(raw)
