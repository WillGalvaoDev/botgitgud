"""B4 — o relatório legado tinha que ser reproduzível, e não era.

`test_legacy_report_matches_golden_snapshot` falhava ~1 vez a cada 8 execuções
da suíte completa e passava isolado, com a mesma árvore e o mesmo comando. A
causa está provada em `docs/v1-readiness-determinism.md`: `reference_players`
chega na ordem de conclusão das threads (`as_completed` em
`fetch_top_logs_for_cds`), essa ordem se propaga até as chaves de `profile`, e
`discover_clean_major_cds` ordenava com um sort **estável** cuja chave era só a
presença — então dois cooldowns empatados herdavam o escalonamento do
`ThreadPoolExecutor`.

Estes testes não dependem de "rodar até falhar": eles impõem a variação de ordem
diretamente, o que torna a regressão determinística de detectar.

Nada aqui toca a rede (cassetes) nem o Discord.
"""

from __future__ import annotations

import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from legacy_runner import isolated_legacy_bot

FIXTURE_REPORT_CODE = "PtfBbQKRY9d6zAMC"
FIXTURE_FIGHT_ID = 1
FIXTURE_CHARACTER = "Zarad"

# Amostra do scan de investigação (200 permutações). No suite mantemos um número
# menor: a divergência original aparecia em ~1 de cada 20 permutações, então
# esta amostra a pegaria com folga se o desempate regredisse.
PERMUTATIONS = 24


def _fetch_inputs(tmp_path: Path) -> tuple[Any, list[Any], tuple[Any, ...]]:
    """Busca uma vez; as permutações reusam os MESMOS dados de referência."""
    with isolated_legacy_bot(tmp_path / "fetch") as legacy_bot:
        token = legacy_bot.get_wcl_token()
        user_data = legacy_bot.fetch_player_timeline_data(
            token, FIXTURE_REPORT_CODE, FIXTURE_FIGHT_ID, FIXTURE_CHARACTER
        )
        assert user_data is not None
        refs, matched, min_d, max_d, avg_p, _parses, min_p, max_p = (
            legacy_bot.fetch_top_logs_for_cds(
                token,
                user_data["fight"]["encounter_id"],
                user_data["build"]["class"],
                user_data["build"]["spec"],
                user_data["fight"]["duration_sec"],
            )
        )
    return user_data, refs, (matched, min_d, max_d, avg_p, min_p, max_p)


def _report_for(
    scratch: Path, user_data: Any, refs: list[Any], rest: tuple[Any, ...]
) -> tuple[str, list[int]]:
    """Módulo novo a cada chamada: `discover_clean_major_cds` muta
    `LOCAL_SPELL_DB`, então reusar a instância contaminaria a comparação com
    estado, e não com ordem.
    """
    matched, min_d, max_d, avg_p, min_p, max_p = rest
    with isolated_legacy_bot(scratch) as legacy_bot:
        profile = legacy_bot.build_cd_reference_profile(refs)
        major_cds = legacy_bot.discover_clean_major_cds(profile)
        comparisons = legacy_bot.compare_major_cds_clean(user_data, profile, major_cds)
        report = legacy_bot.generate_coach_report_string(
            FIXTURE_CHARACTER, user_data, matched, min_d, max_d, avg_p, min_p, max_p, comparisons
        )
    return report, list(major_cds)


def test_report_is_identical_under_any_reference_arrival_order(
    mock_http: None, tmp_path: Path
) -> None:
    """A prova direta da regressão: mesma entrada lógica, ordens de chegada
    diferentes, relatório byte a byte igual.

    Falharia contra 781bc71 — na investigação, 3 das primeiras 60 permutações
    trocavam a posição de "Summon Demonic Tyrant" e "Call Dreadstalkers".
    """
    user_data, refs, rest = _fetch_inputs(tmp_path)
    baseline, baseline_order = _report_for(tmp_path / "baseline", user_data, list(refs), rest)

    rng = random.Random(20260826)
    for i in range(PERMUTATIONS):
        shuffled = list(refs)
        rng.shuffle(shuffled)
        report, order = _report_for(tmp_path / f"perm{i}", user_data, shuffled, rest)
        assert order == baseline_order, f"ordem dos CDs mudou na permutação {i}"
        assert report == baseline, f"relatório mudou na permutação {i}"


def test_report_is_identical_under_real_thread_completion_order(
    mock_http: None, tmp_path: Path
) -> None:
    """O mesmo invariante, mas com o escalonador de verdade decidindo a ordem.

    Nada de monkeypatch em `as_completed`: um `ThreadPoolExecutor` real, com
    atrasos artificiais deliberadamente invertidos, produz a ordem de conclusão
    — exatamente o mecanismo que causava o flake.
    """
    user_data, refs, rest = _fetch_inputs(tmp_path)
    baseline, _ = _report_for(tmp_path / "baseline", user_data, list(refs), rest)

    for case, delay_for in enumerate(
        (
            lambda i: 0.002 * (len(refs) - i),  # conclui na ordem inversa
            lambda i: 0.002 * (i % 5),  # conclui embaralhado por blocos
        )
    ):
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(_delayed_identity, ref, delay_for(i)): i
                for i, ref in enumerate(refs)
            }
            arrival = [future.result() for future in as_completed(futures)]

        assert len(arrival) == len(refs)
        report, _ = _report_for(tmp_path / f"threads{case}", user_data, arrival, rest)
        assert report == baseline, f"o escalonamento de threads mudou a saída (caso {case})"


def _delayed_identity(ref: Any, delay_s: float) -> Any:
    time.sleep(delay_s)
    return ref


# -- desempate canônico, isolado da rede e do fixture --------------------------------


def _profile_entry(spell_id: int, presence: float, avg_cd: float = 60.0) -> dict[str, Any]:
    return {
        "id": spell_id,
        "name": f"Spell {spell_id}",
        "category": "trackable",
        "source": "wcl",
        "presence": presence,
        "avg_cd_duration": avg_cd,
    }


@pytest.mark.parametrize(
    "insertion_order",
    [(104316, 265187), (265187, 104316)],
    ids=["dreadstalkers-first", "tyrant-first"],
)
def test_tied_presence_is_broken_by_spell_id_not_by_insertion_order(
    tmp_path: Path, insertion_order: tuple[int, int]
) -> None:
    """A regra canônica: presença DESC, e spell_id ASC como desempate.

    A ordem de inserção no `profile` é o que a ordem de chegada das referências
    controlava. Com o desempate por identidade ela deixa de importar.
    """
    with isolated_legacy_bot(tmp_path / "scratch") as legacy_bot:
        profile = {spell_id: _profile_entry(spell_id, 1.0) for spell_id in insertion_order}
        assert legacy_bot.discover_clean_major_cds(profile) == [104316, 265187]


def test_primary_sort_still_wins_over_the_tie_breaker(tmp_path: Path) -> None:
    """O desempate é só desempate: presença continua sendo o critério de negócio,
    e um spell_id menor não pode subir na frente de uma presença maior.
    """
    with isolated_legacy_bot(tmp_path / "scratch") as legacy_bot:
        profile = {
            111: _profile_entry(111, 0.75),
            999: _profile_entry(999, 1.00),
            222: _profile_entry(222, 0.90),
        }
        assert legacy_bot.discover_clean_major_cds(profile) == [999, 222, 111]


def test_eligibility_is_unchanged_by_the_tie_breaker(tmp_path: Path) -> None:
    """O desempate não pode alterar QUEM entra na lista — só a ordem."""
    with isolated_legacy_bot(tmp_path / "scratch") as legacy_bot:
        profile = {
            100: _profile_entry(100, 1.0, avg_cd=60.0),  # elegível
            200: _profile_entry(200, 0.50, avg_cd=60.0),  # presença abaixo de 0,70
            300: _profile_entry(300, 1.0, avg_cd=10.0),  # cadência abaixo de 18s
        }
        assert legacy_bot.discover_clean_major_cds(profile) == [100]
