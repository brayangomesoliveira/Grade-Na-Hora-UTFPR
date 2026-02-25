from __future__ import annotations

import threading
from collections.abc import Callable


class CancelledError(RuntimeError):
    """Erro de cancelamento explícito pelo usuário."""


class CancelToken:
    """Token thread-safe para cancelamento cooperativo e forçado."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[], None]] = []

    def cancel(self) -> None:
        callbacks: list[Callable[[], None]]
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            callbacks = list(self._callbacks)
        for cb in callbacks:
            try:
                cb()
            except Exception:
                # Cancelamento deve ser resiliente; ignora falhas de shutdown.
                pass

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise CancelledError("Operacao cancelada pelo usuario")

    def register_cancel_callback(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if self._event.is_set():
                run_now = True
            else:
                self._callbacks.append(callback)
                run_now = False
        if run_now:
            try:
                callback()
            except Exception:
                pass
