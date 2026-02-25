from src.core.models import Turma
from src.core.schedule import build_schedule, conflict_uids, parse_horarios


def _mk_turma(cod: str, turma: str, raw: str) -> Turma:
    return Turma(
        disciplina_codigo=cod,
        disciplina_nome=f"Disc {cod}",
        turma_codigo=turma,
        horario_raw=raw,
        horarios=parse_horarios(raw),
    )


def test_build_schedule_detecta_conflito_mesma_celula() -> None:
    t1 = _mk_turma("ELT73B", "S01", "5T2(CE-208)")
    t2 = _mk_turma("MAT7AL", "S11", "5T2(CE-308)")
    result = build_schedule([t1, t2])
    assert len(result.conflitos) == 1
    assert result.conflitos[0].cell_code == "5T2"


def test_conflict_uids_retorna_ids_das_turmas_em_conflito() -> None:
    t1 = _mk_turma("AAA111", "S01", "2M1")
    t2 = _mk_turma("BBB222", "S02", "2M1")
    t3 = _mk_turma("CCC333", "S03", "3M1")
    result = build_schedule([t1, t2, t3])
    ids = conflict_uids(result)
    assert t1.uid() in ids
    assert t2.uid() in ids
    assert t3.uid() not in ids
