import pytest

from src.core.schedule import parse_horarios


def test_parse_token_simples_com_sala() -> None:
    slots = parse_horarios("5T2(CE-208)")
    assert len(slots) == 1
    slot = slots[0]
    assert slot.day_index == 3
    assert slot.period == "T"
    assert slot.slot == 2
    assert slot.room == "CE-208"


def test_parse_multiplos_tokens_separados_por_hifen() -> None:
    slots = parse_horarios("5T2(CE-208) - 5T3(CE-208) - 6T4(CE-308)")
    assert [s.codigo for s in slots] == ["5T2", "5T3", "6T4"]


def test_parse_aceita_asterisco_na_sala_e_remove() -> None:
    slots = parse_horarios("3T3(*EK-307)")
    assert slots[0].room == "EK-307"


def test_parse_faixa_de_aula_expande_slots() -> None:
    slots = parse_horarios("6N1-2(EK-307)")
    assert [s.codigo for s in slots] == ["6N1", "6N2"]


def test_parse_rejeita_trecho_nao_reconhecido() -> None:
    with pytest.raises(ValueError, match="Trecho nao reconhecido"):
        parse_horarios("5T2(CE-208) texto_solto")


def test_parse_rejeita_slot_fora_do_limite_noite() -> None:
    with pytest.raises(ValueError, match="fora do limite"):
        parse_horarios("6N6")
