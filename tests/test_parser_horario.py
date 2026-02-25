from pathlib import Path
import sys

# Garante import dos modulos em `src/` sem depender de instalacao editavel nesta etapa.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.domain.parser_horario import parse_horario_raw


def test_parse_multiplos_itens_com_salas_normais() -> None:
    slots = parse_horario_raw("5T2(CE-208) - 6T4(CE-308)")

    assert len(slots) == 2
    assert slots[0].dia_numero == 5
    assert slots[0].dia_label == "Qui"
    assert slots[0].turno == "T"
    assert slots[0].aula == 2
    assert slots[0].codigo == "5T2"
    assert slots[0].sala == "CE-208"

    assert slots[1].dia_numero == 6
    assert slots[1].dia_label == "Sex"
    assert slots[1].turno == "T"
    assert slots[1].aula == 4
    assert slots[1].codigo == "6T4"
    assert slots[1].sala == "CE-308"


def test_parse_sala_com_asterisco() -> None:
    slots = parse_horario_raw("3T3(*EK-307)")

    assert len(slots) == 1
    assert slots[0].dia_numero == 3
    assert slots[0].dia_label == "Ter"
    assert slots[0].turno == "T"
    assert slots[0].aula == 3
    assert slots[0].codigo == "3T3"
    assert slots[0].sala == "EK-307"


def test_parse_com_espacos_inconsistentes_e_hifens() -> None:
    texto = " 3 T 1 - 2   ( C-201 ) ; 5N3-4  -  LAB-INFO "
    slots = parse_horario_raw(texto)

    assert len(slots) == 4

    assert [slot.codigo for slot in slots] == ["3T1", "3T2", "5N3", "5N4"]
    assert [slot.sala for slot in slots] == ["C-201", "C-201", "LAB-INFO", "LAB-INFO"]