from __future__ import annotations

import logging
import re

from .models import HorarioSlot

logger = logging.getLogger(__name__)

DIA_LABELS: dict[int, str] = {
    2: "Seg",
    3: "Ter",
    4: "Qua",
    5: "Qui",
    6: "Sex",
    7: "Sab",
}

_AULAS_MAX_POR_TURNO: dict[str, int] = {
    "M": 6,
    "T": 6,
    "N": 5,
}

# Aceita formatos como:
# - 5T2(CE-208)
# - 3T3(*EK-307)
# - 6N1-2 (CE-101)
# - 5N3-4 - LAB-INFO
_ITEM_RE = re.compile(
    r"""
    ^\s*
    (?P<dia>[2-7])\s*
    (?P<turno>[MTN])\s*
    (?P<aulas>\d+(?:\s*-\s*\d+)?)
    \s*
    (?:
        \(\s*(?P<sala_paren>[^()]+?)\s*\)
        |
        -\s*(?P<sala_plain>[^()]+?)
    )?
    \s*$
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Separadores de itens: ';', ',', '|' ou hifen com espacos seguido de novo token (ex.: " - 6T4(...)")
_SPLIT_RE = re.compile(r"\s*(?:[;,|]|\s+-\s+(?=[2-7]\s*[MTN]))\s*", re.IGNORECASE)
_HYPHEN_SPACES_RE = re.compile(r"\s*-\s*")
_SPACES_RE = re.compile(r"\s+")


def _normalize_sala(sala_raw: str | None) -> str | None:
    if sala_raw is None:
        return None

    sala = sala_raw.strip().replace("*", "")
    # Regra solicitada: remover espacos da sala.
    sala = _SPACES_RE.sub("", sala)
    return sala or None


def _parse_aulas(aulas_raw: str, *, turno: str, item: str) -> list[int]:
    texto = _HYPHEN_SPACES_RE.sub("-", aulas_raw.strip())
    partes = texto.split("-", maxsplit=1)

    try:
        if len(partes) == 1:
            aulas = [int(partes[0])]
        else:
            inicio = int(partes[0])
            fim = int(partes[1])
            if fim < inicio:
                raise ValueError("faixa invertida")
            aulas = list(range(inicio, fim + 1))
    except ValueError as exc:
        raise ValueError(f"Aulas invalidas no item '{item}': '{aulas_raw}'") from exc

    max_aula = _AULAS_MAX_POR_TURNO[turno]
    for aula in aulas:
        if aula < 1 or aula > max_aula:
            raise ValueError(
                f"Aula fora do limite para turno {turno} no item '{item}': {aula} "
                f"(permitido 1..{max_aula})"
            )

    return aulas


def parse_horario_raw(horario_raw: str) -> list[HorarioSlot]:
    """Converte `horarioRaw` em lista de `HorarioSlot` (slots individuais)."""
    if not isinstance(horario_raw, str):
        raise ValueError("horario_raw deve ser uma string")

    texto = horario_raw.strip()
    if not texto:
        raise ValueError("horario_raw vazio")

    itens = [parte for parte in _SPLIT_RE.split(texto) if parte and parte.strip()]
    if not itens:
        raise ValueError("Nenhum item de horario encontrado em horario_raw")

    slots: list[HorarioSlot] = []
    for idx, item in enumerate(itens, start=1):
        match = _ITEM_RE.match(item)
        if not match:
            logger.warning("Falha ao parsear item de horario: %s", item)
            raise ValueError(
                f"Item de horario invalido na posicao {idx}: '{item}'. "
                "Exemplos validos: '5T2(CE-208)' ou '3T3(*EK-307)'."
            )

        dia_numero = int(match.group("dia"))
        dia_label = DIA_LABELS.get(dia_numero)
        if dia_label is None:
            raise ValueError(f"Dia fora do intervalo permitido (2..7): {dia_numero}")

        turno = match.group("turno").upper()
        sala = _normalize_sala(match.group("sala_paren") or match.group("sala_plain"))
        aulas = _parse_aulas(match.group("aulas"), turno=turno, item=item.strip())

        for aula in aulas:
            codigo = f"{dia_numero}{turno}{aula}"
            slot = HorarioSlot(
                dia_numero=dia_numero,
                dia_label=dia_label,
                turno=turno,
                aula=aula,
                codigo=codigo,
                sala=sala,
                raw_item=item.strip(),
            )
            logger.debug("HorarioSlot gerado: %s", slot)
            slots.append(slot)

    return slots