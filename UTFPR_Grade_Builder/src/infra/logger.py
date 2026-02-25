from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT_DIR / "logs"
SCREENSHOT_DIR = LOG_DIR / "screenshots"
HTML_DIR = LOG_DIR / "html"
LOG_FILE = LOG_DIR / "app.log"


def ensure_log_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    HTML_DIR.mkdir(parents=True, exist_ok=True)


def setup_logging(debug: bool = False) -> None:
    """Configura logging com rotação em arquivo + console (idempotente)."""
    ensure_log_dirs()
    root = logging.getLogger()
    if getattr(root, "_utfpr_grade_builder_logger_ready", False):
        if debug:
            root.setLevel(logging.DEBUG)
        return

    root.setLevel(logging.DEBUG if debug else logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(threadName)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=1_500_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if debug else logging.INFO)
    console.setFormatter(fmt)

    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console)
    root._utfpr_grade_builder_logger_ready = True  # type: ignore[attr-defined]


def make_debug_artifact_paths(prefix: str) -> tuple[Path, Path]:
    ensure_log_dirs()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in prefix)[:48]
    base_name = f"{stamp}_{safe}"
    return (SCREENSHOT_DIR / f"{base_name}.png", HTML_DIR / f"{base_name}.html")


def open_logs_folder() -> None:
    """Abre a pasta de logs no explorer/finder/gerenciador padrão."""
    ensure_log_dirs()
    if sys.platform.startswith("win"):
        os.startfile(str(LOG_DIR))  # type: ignore[attr-defined]
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(LOG_DIR)])
        return
    subprocess.Popen(["xdg-open", str(LOG_DIR)])
