from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Period = Literal["M", "T", "N"]


@dataclass(slots=True)
class HorarioSlot:
    """Slot de horário normalizado (ex.: 5T2, 3M1)."""

    codigo: str
    day_number: int
    day_index: int  # 0..5 (Seg..Sab)
    day_label: str
    period: Period
    slot: int
    room: str | None = None
    raw_token: str = ""

    def cell_key(self) -> tuple[str, int, int]:
        return (self.period, self.slot, self.day_index)

    def to_dict(self) -> dict[str, Any]:
        return {
            "codigo": self.codigo,
            "day_number": self.day_number,
            "day_index": self.day_index,
            "day_label": self.day_label,
            "period": self.period,
            "slot": self.slot,
            "room": self.room,
            "raw_token": self.raw_token,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HorarioSlot":
        return cls(
            codigo=str(data["codigo"]),
            day_number=int(data["day_number"]),
            day_index=int(data["day_index"]),
            day_label=str(data["day_label"]),
            period=str(data["period"]).upper(),  # type: ignore[arg-type]
            slot=int(data["slot"]),
            room=data.get("room"),
            raw_token=str(data.get("raw_token", "")),
        )


@dataclass(slots=True)
class Turma:
    """Linha da tabela de turmas abertas."""

    disciplina_codigo: str
    disciplina_nome: str
    turma_codigo: str
    horario_raw: str
    horarios: list[HorarioSlot] = field(default_factory=list)
    professor: str | None = None
    vagas_total: int | None = None
    vagas_calouros: int | None = None
    status: str | None = None
    prioridade: str | None = None
    creditos: int | None = None

    def uid(self) -> str:
        return f"{self.disciplina_codigo}|{self.turma_codigo}|{self.horario_raw}".strip()

    def creditos_estimados(self) -> int:
        return int(self.creditos) if self.creditos is not None else len(self.horarios)

    def horarios_compactos(self) -> str:
        partes = []
        for h in self.horarios:
            if h.room:
                partes.append(f"{h.codigo}({h.room})")
            else:
                partes.append(h.codigo)
        return " ".join(partes)

    def resumo_linha(self) -> str:
        prof = self.professor or "-"
        vagas = "-" if self.vagas_total is None else str(self.vagas_total)
        status = self.status or "-"
        return (
            f"{self.disciplina_codigo} - {self.disciplina_nome} - {self.turma_codigo} - "
            f"{self.horarios_compactos()} - Prof: {prof} - Vagas: {vagas} - Status: {status}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "disciplina_codigo": self.disciplina_codigo,
            "disciplina_nome": self.disciplina_nome,
            "turma_codigo": self.turma_codigo,
            "horario_raw": self.horario_raw,
            "horarios": [h.to_dict() for h in self.horarios],
            "professor": self.professor,
            "vagas_total": self.vagas_total,
            "vagas_calouros": self.vagas_calouros,
            "status": self.status,
            "prioridade": self.prioridade,
            "creditos": self.creditos,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Turma":
        return cls(
            disciplina_codigo=str(data.get("disciplina_codigo", "")),
            disciplina_nome=str(data.get("disciplina_nome", "")),
            turma_codigo=str(data.get("turma_codigo", "")),
            horario_raw=str(data.get("horario_raw", "")),
            horarios=[HorarioSlot.from_dict(item) for item in data.get("horarios", [])],
            professor=data.get("professor"),
            vagas_total=data.get("vagas_total"),
            vagas_calouros=data.get("vagas_calouros"),
            status=data.get("status"),
            prioridade=data.get("prioridade"),
            creditos=data.get("creditos"),
        )


@dataclass(slots=True)
class ScheduleConflict:
    """Conflito em uma célula da grade."""

    day_index: int
    day_label: str
    period: str
    slot: int
    turmas: list[Turma]

    @property
    def cell_code(self) -> str:
        return f"{self.day_index + 2}{self.period}{self.slot}"


@dataclass(slots=True)
class ScheduleBuildResult:
    """Resultado da montagem da grade."""

    grid: dict[str, dict[int, dict[int, list[Turma]]]]
    conflitos: list[ScheduleConflict]
    creditos_usados: int

    @classmethod
    def empty(cls) -> "ScheduleBuildResult":
        return cls(grid={}, conflitos=[], creditos_usados=0)
