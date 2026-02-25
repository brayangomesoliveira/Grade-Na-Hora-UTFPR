from __future__ import annotations

from dataclasses import dataclass

from .models import TurmaAberta

GRID_DIAS = [2, 3, 4, 5, 6, 7]
GRID_LINHAS = [*(f"M{i}" for i in range(1, 7)), *(f"T{i}" for i in range(1, 7)), *(f"N{i}" for i in range(1, 6))]


@dataclass(slots=True)
class GradeResultado:
    ocupacao: dict[str, list[TurmaAberta]]
    conflitos: dict[str, list[TurmaAberta]]
    creditos_usados: int


def turma_uid(turma: TurmaAberta) -> str:
    return f"{turma.codigo}|{turma.turma}"


def creditos_por_turma(turma: TurmaAberta) -> int:
    return len(turma.horarios)


def montar_grade(turmas: list[TurmaAberta]) -> GradeResultado:
    ocupacao: dict[str, list[TurmaAberta]] = {}
    creditos = 0
    for turma in turmas:
        creditos += creditos_por_turma(turma)
        for slot in turma.horarios:
            ocupacao.setdefault(slot.codigo, []).append(turma)

    conflitos = {
        codigo: lista
        for codigo, lista in ocupacao.items()
        if len({turma_uid(t) for t in lista}) > 1
    }
    return GradeResultado(ocupacao=ocupacao, conflitos=conflitos, creditos_usados=creditos)


def turma_tem_conflito(turma: TurmaAberta, resultado: GradeResultado) -> bool:
    return any(slot.codigo in resultado.conflitos for slot in turma.horarios)