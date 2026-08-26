"""B4 — o mesmo invariante do relatório legado, verificado no pipeline atual.

O flake foi encontrado no golden legado, mas a pergunta que importa para a
release é outra: *o pipeline de produção depende da ordem de conclusão das
threads?* Estes testes fixam a resposta como "não", nos dois pontos onde ela
poderia mudar — a normalização de ordem em `fetch_many` e o desempate de
presença em `discover_eligible_spell_ids`.

Nenhum destes testes toca rede.
"""

from __future__ import annotations

import random
import time
from typing import Any

import pytest

from botgitgud.analysis.cohort import COHORT_MIN_HARD
from botgitgud.analysis.profile import build_cd_reference_profile, discover_eligible_spell_ids
from botgitgud.domain.models import FightRef, PlayerBuild, PlayerLog
from botgitgud.ingest.log_fetcher import LogFetcher, LogRequest

FIREBALL = 133
BIG_CD = 190319
_DEFAULT_TALENTS = frozenset({(100, 1), (101, 1)})


def _log(
    *,
    name: str,
    report_code: str = "ABCDEFGHIJKLMNOP",
    duration_s: float = 400.0,
    cast_timeline: dict[int, tuple[float, ...]] | None = None,
) -> PlayerLog:
    fight = FightRef(
        report_code=report_code,
        fight_id=1,
        encounter_id=3179,
        boss_name="Fallen-King Salhadaar",
        difficulty=5,
        duration_s=duration_s,
        kill=True,
    )
    build = PlayerBuild(
        character_name=name,
        server="Azralon",
        class_name="Warlock",
        spec_name="Demonology",
        role="dps",
        item_level=283.0,
        talent_hash=None,
        tier_pieces=4,
        talent_pairs=_DEFAULT_TALENTS,
    )
    return PlayerLog(
        fight=fight,
        build=build,
        dps=100000.0,
        percentile=50.0,
        cast_timeline=cast_timeline or {},
    )


# -- 1. `fetch_many` normaliza a ordem de conclusão -----------------------------------


class _SlowStore:
    """Store falso: a leitura de cache é um miss, e a escrita é registrada."""

    def __init__(self) -> None:
        self.written: list[str] = []

    def read_log(self, _report_code: str, _fight_id: int, _player: str) -> PlayerLog | None:
        return None

    def write_log(self, log: PlayerLog) -> None:
        self.written.append(log.build.character_name)


class _DelayedFetcher(LogFetcher):
    """Cada referência demora um tempo diferente, então as threads concluem
    fora da ordem de entrada — que é exatamente o mecanismo do flake legado.
    """

    def __init__(self, store: Any, delays: dict[str, float]) -> None:
        super().__init__(client=None, store=store, catalog=None)  # type: ignore[arg-type]
        self._delays = delays

    def _fetch_from_api(  # type: ignore[override]
        self, report_code: str, fight_id: int, player: str, **_kwargs: Any
    ) -> PlayerLog:
        time.sleep(self._delays.get(player, 0.0))
        return _log(name=player, report_code=report_code)


@pytest.mark.parametrize(
    "delays",
    [
        {"Ref0": 0.02, "Ref1": 0.015, "Ref2": 0.01, "Ref3": 0.005, "Ref4": 0.0},
        {"Ref0": 0.0, "Ref1": 0.02, "Ref2": 0.0, "Ref3": 0.02, "Ref4": 0.0},
    ],
    ids=["reverse-completion", "interleaved-completion"],
)
def test_fetch_many_returns_request_order_not_completion_order(
    delays: dict[str, float],
) -> None:
    """O invariante que protege TODO o pipeline: a ordem de saída é a ordem
    pedida, nunca a ordem em que as threads terminaram.
    """
    store = _SlowStore()
    fetcher = _DelayedFetcher(store, delays)
    refs = [LogRequest("ABCDEFGHIJKLMNOP", 1, f"Ref{i}") for i in range(5)]

    result = fetcher.fetch_many(refs, max_workers=5)

    assert [log.build.character_name for log in result] == [ref.player for ref in refs]
    # As escritas seguem a conclusão (é o que a concorrência entrega); o
    # contrato de ORDEM vive na lista devolvida, e é só ela que o pipeline usa.
    assert sorted(store.written) == [f"Ref{i}" for i in range(5)]


# -- 2. desempate canônico de presença ------------------------------------------------


def _cohort_with_tied_presence(order: list[int]) -> list[PlayerLog]:
    """Duas spells presentes em 100% da coorte: empate perfeito de presença.

    `order` controla em qual ordem elas aparecem no `cast_timeline` de cada
    log — o análogo, no pipeline atual, da ordem de inserção que a chegada das
    threads controlava no legado.
    """
    times = {FIREBALL: (10.0, 130.0, 250.0, 370.0), BIG_CD: (20.0, 140.0, 260.0, 380.0)}
    return [
        _log(
            name=f"Ref{i}",
            report_code=f"REFCODE{i:09d}",
            cast_timeline={spell: times[spell] for spell in order},
        )
        for i in range(COHORT_MIN_HARD)
    ]


@pytest.mark.parametrize(
    "insertion_order",
    [[FIREBALL, BIG_CD], [BIG_CD, FIREBALL]],
    ids=["fireball-first", "bigcd-first"],
)
def test_tied_presence_resolves_to_ascending_spell_id(insertion_order: list[int]) -> None:
    logs = _cohort_with_tied_presence(insertion_order)
    profile, _n = build_cd_reference_profile(logs, target_duration_s=400.0)

    eligible = discover_eligible_spell_ids(profile)

    # O empate precisa ser real: as duas spells elegíveis, mesma presença.
    assert {profile[s].presence for s in eligible} == {1.0}
    assert eligible == [FIREBALL, BIG_CD] == sorted([FIREBALL, BIG_CD])
    # E a ordem das chaves do profile não pode mover nada.
    assert eligible == discover_eligible_spell_ids(
        dict(reversed(list(profile.items())))  # type: ignore[arg-type]
    )


def test_shuffled_reference_order_produces_the_same_eligible_list() -> None:
    """Permutação das referências não pode mover nada no pipeline atual."""
    logs = _cohort_with_tied_presence([FIREBALL, BIG_CD])
    baseline, _ = build_cd_reference_profile(logs, target_duration_s=400.0)
    expected = discover_eligible_spell_ids(baseline)

    rng = random.Random(4)
    for _ in range(12):
        shuffled = list(logs)
        rng.shuffle(shuffled)
        profile, _n = build_cd_reference_profile(shuffled, target_duration_s=400.0)
        assert discover_eligible_spell_ids(profile) == expected
        assert {s: profile[s].presence for s in profile} == {
            s: baseline[s].presence for s in baseline
        }


# -- 3. a ordem do pool de candidatos é um contrato, não um acaso do SQL --------------


def test_candidate_pool_round_trips_in_a_guaranteed_order(tmp_path: Any) -> None:
    """`read_candidate_pool` alimenta a ordem dos logs de referência, que
    alimenta a ordem do relatório. Sem `ORDER BY`, o SQL não promete ordem
    nenhuma — e o DuckDB varre em paralelo. Este teste fixa o contrato.
    """
    from botgitgud.domain.models import RankingCandidate
    from botgitgud.ingest.store import Store

    store = Store(tmp_path / "data")
    written = [
        RankingCandidate(
            report_code=f"CODE{i:012d}", fight_id=1, player_name=f"P{i}", duration_s=300.0 + i
        )
        # Ordem de escrita deliberadamente NÃO alfabética e NÃO ordenada por
        # nenhuma coluna: só a ordem de inserção reproduz esta lista.
        for i in (7, 2, 9, 0, 4, 1)
    ]
    store.write_candidate_pool("cohort-order", written)

    for _ in range(5):
        pool = store.read_candidate_pool("cohort-order")
        assert pool is not None
        assert [c.player_name for c in pool] == [c.player_name for c in written]
    store.close()
