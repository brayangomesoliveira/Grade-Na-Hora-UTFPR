from __future__ import annotations

import logging
import re

from .models import HorarioSlot, ScheduleBuildResult, ScheduleConflict, Turma

logger = logging.getLogger(__name__)

DIA_LABELS: dict[int, str] = {
    2: "Seg",
    3: "Ter",
    4: "Qua",
    5: "Qui",
    6: "Sex",
    7: "Sab",
}

DIA_LABELS_LONG: dict[int, str] = {
    2: "Segunda",
    3: "Terca",
    4: "Quarta",
    5: "Quinta",
    6: "Sexta",
    7: "Sabado",
}

PERIOD_MAX_SLOTS = {"M": 6, "T": 6, "N": 5}
PERIOD_ORDER = ("M", "T", "N")

_TOKEN_RE = re.compile(
    r"""
    (?P<dia>[2-7])\s*
    (?P<periodo>[MTN])\s*
    (?P<aula>\d(?:\s*-\s*\d)?)
    (?:\s*\(\s*(?P<sala>[^()]+?)\s*\))?
    """,
    re.VERBOSE | re.IGNORECASE,
)
_SEP_ONLY_RE = re.compile(r"^[\s,;|/\-]*$")
_SPACE_RE = re.compile(r"\s+")
_HYPHEN_RE = re.compile(r"\s*-\s*")


def _normalize_room(raw_room: str | None) -> str | None:
    if not raw_room:
        return None
    room = raw_room.replace("*", "").strip()
    room = _SPACE_RE.sub("", room)
    return room or None


def _expand_slot_value(raw_value: str, period: str, token: str) -> list[int]:
    value = _HYPHEN_RE.sub("-", raw_value.strip())
    parts = value.split("-", maxsplit=1)
    try:
        if len(parts) == 1:
            slots = [int(parts[0])]
        else:
            start = int(parts[0])
            end = int(parts[1])
            if end < start:
                raise ValueError("faixa invertida")
            slots = list(range(start, end + 1))
    except ValueError as exc:
        raise ValueError(f"Aula invalida no token '{token}': '{raw_value}'") from exc

    max_slot = PERIOD_MAX_SLOTS[period]
    for slot in slots:
        if slot < 1 or slot > max_slot:
            raise ValueError(
                f"Aula fora do limite do turno {period} no token '{token}': {slot} "
                f"(permitido 1..{max_slot})"
            )
    return slots


def parse_horarios(raw: str) -> list[HorarioSlot]:
    """Parseia horários UTFPR em slots normalizados.

    Exemplos: `5T2(CE-208)`, `4M1`, `6N2`, `5T2(CE-208) - 5T3(CE-208)`.
    """
    if not isinstance(raw, str):
        raise ValueError("raw deve ser string")

    text = raw.strip()
    if not text:
        return []

    matches = list(_TOKEN_RE.finditer(text))
    if not matches:
        raise ValueError(f"Nenhum token de horario reconhecido em: '{raw}'")

    slots: list[HorarioSlot] = []
    cursor = 0
    for match in matches:
        gap = text[cursor : match.start()]
        if gap and not _SEP_ONLY_RE.fullmatch(gap):
            raise ValueError(f"Trecho nao reconhecido no horario: '{gap.strip()}'")

        token = match.group(0).strip()
        day_number = int(match.group("dia"))
        period = match.group("periodo").upper()
        day_label = DIA_LABELS[day_number]
        day_index = day_number - 2
        room = _normalize_room(match.group("sala"))
        for slot_num in _expand_slot_value(match.group("aula"), period, token):
            slots.append(
                HorarioSlot(
                    codigo=f"{day_number}{period}{slot_num}",
                    day_number=day_number,
                    day_index=day_index,
                    day_label=day_label,
                    period=period,  # type: ignore[arg-type]
                    slot=slot_num,
                    room=room,
                    raw_token=token,
                )
            )
        cursor = match.end()

    tail = text[cursor:]
    if tail and not _SEP_ONLY_RE.fullmatch(tail):
        raise ValueError(f"Trecho nao reconhecido no horario: '{tail.strip()}'")
    return slots


def init_empty_grid() -> dict[str, dict[int, dict[int, list[Turma]]]]:
    grid: dict[str, dict[int, dict[int, list[Turma]]]] = {}
    for period in PERIOD_ORDER:
        grid[period] = {}
        for slot in range(1, PERIOD_MAX_SLOTS[period] + 1):
            grid[period][slot] = {day_index: [] for day_index in range(6)}
    return grid


def calculate_credits(turmas: list[Turma]) -> int:
    """Usa créditos do portal se houver; caso contrário, total de slots semanais."""
    return sum(t.creditos_estimados() for t in turmas)


def build_schedule(turmas: list[Turma]) -> ScheduleBuildResult:
    """Monta grade e detecta conflitos.

    Estrutura interna: `grid[periodo][slot][day_index] = list[Turma]`
    """
    grid = init_empty_grid()
    for turma in turmas:
        for h in turma.horarios:
            if h.period not in grid or h.slot not in grid[h.period] or h.day_index not in grid[h.period][h.slot]:
                logger.warning("Slot fora do padrao ignorado: %s", h)
                continue
            grid[h.period][h.slot][h.day_index].append(turma)

    conflitos: list[ScheduleConflict] = []
    for period, slots in grid.items():
        for slot, days in slots.items():
            for day_index, cell in days.items():
                if len({t.uid() for t in cell}) > 1:
                    conflitos.append(
                        ScheduleConflict(
                            day_index=day_index,
                            day_label=DIA_LABELS[day_index + 2],
                            period=period,
                            slot=slot,
                            turmas=list(cell),
                        )
                    )

    conflitos.sort(key=lambda c: (c.day_index, PERIOD_ORDER.index(c.period), c.slot))
    return ScheduleBuildResult(grid=grid, conflitos=conflitos, creditos_usados=calculate_credits(turmas))


def conflict_uids(result: ScheduleBuildResult) -> set[str]:
    ids: set[str] = set()
    for conflito in result.conflitos:
        ids.update(t.uid() for t in conflito.turmas)
    return ids


def selected_turmas(all_turmas: list[Turma], selected_ids: set[str]) -> list[Turma]:
    return [t for t in all_turmas if t.uid() in selected_ids]


def summarize_selection(turmas: list[Turma], result: ScheduleBuildResult) -> str:
    lines: list[str] = []
    for turma in turmas:
        horarios = ", ".join(
            f"{slot.codigo}({slot.room})" if slot.room else slot.codigo for slot in turma.horarios
        )
        prof = turma.professor or "-"
        lines.append(
            f"{turma.disciplina_codigo} - {turma.turma_codigo} | horarios: {horarios} | prof: {prof}"
        )
    if not lines:
        lines.append("Nenhuma turma selecionada.")
    lines.append("")
    lines.append(f"Creditos/slots usados: {result.creditos_usados}")
    lines.append(f"Conflitos detectados: {len(result.conflitos)}")
    return "\n".join(lines)
