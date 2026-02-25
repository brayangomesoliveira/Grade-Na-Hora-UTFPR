from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class HorarioSlot:
    """Representa um slot unico de aula (ex.: 5T2)."""

    dia_numero: int
    dia_label: str
    turno: str
    aula: int
    codigo: str
    sala: str | None = None
    raw_item: str = ""


@dataclass(slots=True)
class TurmaAberta:
    """Representa uma turma aberta da disciplina no portal."""

    codigo: str
    nome: str
    turma: str
    professor: str | None
    horarioRaw: str
    horarios: list[HorarioSlot] = field(default_factory=list)
    vagas: int | None = None
    prioridade: str | None = None