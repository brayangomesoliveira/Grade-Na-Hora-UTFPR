from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AppStatus(str, Enum):
    IDLE = "IDLE"
    LOGGING = "LOGGING"
    SCRAPING = "SCRAPING"
    READY = "READY"
    ERROR = "ERROR"
    CANCELED = "CANCELED"


@dataclass(slots=True)
class ProgressInfo:
    """Mensagem de progresso enviada por workers para a UI."""

    status: AppStatus
    message: str
    detail: str | None = None
    percent: int | None = None


@dataclass(slots=True)
class LoginRequest:
    """Parâmetros de login enviados pela UI ao worker."""

    ra: str
    password: str
    campus_name: str = "Curitiba"
    portal_course_value: str | None = None
    portal_course_label: str | None = None
    add_prefix_a: bool = True
    debug_browser: bool = False

    @property
    def username(self) -> str:
        cleaned = "".join(ch for ch in self.ra if ch.isalnum())
        if self.add_prefix_a and cleaned and not cleaned.lower().startswith("a"):
            return f"a{cleaned}"
        return cleaned


@dataclass(slots=True)
class AppState:
    """Estado persistente local (sem senha)."""

    selected_ids: list[str] = field(default_factory=list)
    credit_limit: int = 40
    theme: str = "dark"
    debug_browser: bool = False
    add_prefix_a: bool = True
    campus_name: str = "Curitiba"
    portal_course_value: str | None = None
    portal_course_label: str | None = None
    last_cache_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_ids": list(self.selected_ids),
            "credit_limit": int(self.credit_limit),
            "theme": self.theme,
            "debug_browser": bool(self.debug_browser),
            "add_prefix_a": bool(self.add_prefix_a),
            "campus_name": self.campus_name,
            "portal_course_value": self.portal_course_value,
            "portal_course_label": self.portal_course_label,
            "last_cache_path": self.last_cache_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppState":
        return cls(
            selected_ids=[str(v) for v in data.get("selected_ids", [])],
            credit_limit=int(data.get("credit_limit", 40)),
            theme=str(data.get("theme", "dark")),
            debug_browser=bool(data.get("debug_browser", False)),
            add_prefix_a=bool(data.get("add_prefix_a", True)),
            campus_name=str(data.get("campus_name", "Curitiba")),
            portal_course_value=(
                str(data["portal_course_value"])
                if data.get("portal_course_value") not in (None, "")
                else None
            ),
            portal_course_label=(
                str(data["portal_course_label"])
                if data.get("portal_course_label") not in (None, "")
                else None
            ),
            last_cache_path=data.get("last_cache_path"),
        )
