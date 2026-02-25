from __future__ import annotations

import argparse
import logging
import os
import sys

from PySide6.QtWidgets import QApplication

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*_args, **_kwargs):  # type: ignore[no-redef]
        return False

from src.infra.logger import setup_logging
from src.ui.main_window import MainWindow
from src.ui.styles import app_stylesheet

logger = logging.getLogger(__name__)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="UTFPR Grade Builder (PySide6 + Playwright)")
    parser.add_argument(
        "--smoke-ms",
        type=int,
        default=None,
        help="Abre a UI e fecha automaticamente apos N ms (teste rapido).",
    )
    return parser


def main() -> int:
    load_dotenv()
    setup_logging(debug=bool(int(os.getenv("UTFPR_DEBUG_BROWSER", "0"))))

    app = QApplication(sys.argv)
    app.setApplicationName("UTFPR Grade Builder")
    app.setStyleSheet(app_stylesheet())

    args = build_arg_parser().parse_args()
    window = MainWindow(smoke_ms=args.smoke_ms)
    window.show()

    logger.info("Aplicativo iniciado")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
