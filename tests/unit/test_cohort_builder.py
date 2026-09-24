"""T1.7 — tests for the batch cohort-building logic behind `build-cohort`.
Reuses test_pipeline.py's dispatch transport/response builders (same
fake-httpx-transport pattern as test_log_fetcher.py/test_pipeline.py).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, SupportsIndex, cast

import pytest
from test_log_fetcher import _events_response, _meta_response, _percentile_response
from test_pipeline import _build_deps, _DispatchTransport, _zone_partitions_response

from botgitgud.analysis.cohort import duration_bucket_bounds, duration_bucket_id
from botgitgud.analysis.cohort_builder import build_cohorts
from botgitgud.analysis.cold_build import ImpossibleColdBuildPolicy
from botgitgud.domain.models import CohortCriteria, RankingCandidate
from botgitgud.errors import CohortDeferredBudget, RateLimitBudgetExceeded
from botgitgud.ingest.rankings import partition_logs_by_difficulty

ENCOUNTER_ID = 3179


def test_inv_cohort_diff_for_heroic_encounter_3421(real_corpus: list[Any]) -> None:
    mixed = [log for log in real_corpus if log.fight.encounter_id == 3421]
    assert any(log.fight.difficulty == 4 for log in mixed)
    kept, discarded = partition_logs_by_difficulty(4, mixed)
    assert kept
    assert discarded  # proves the corpus actually exercises the old mixed pool
    assert all(log.fight.difficulty == 4 for log in kept)


def test_budget_defer_makes_zero_construction_queries(tmp_path: Path) -> None:
    transport = _DispatchTransport(_responses_for({100.0: 8}))
    deps = _build_deps(tmp_path, transport)
    # build-cohort e PREWARM: quem o governa e a margem de batch. Aqui o piso
    # protegido consome todo o teto da conta do fixture, entao nem uma unica
    # referencia cabe.
    #
    # Nota: o fixture reporta pointsSpentThisHour=0, logo available == limit.
    # Nesse regime "adiado agora" e "impossivel por configuracao" coincidem
    # matematicamente — o que importa aqui, e o que este teste guarda, e que
    # nenhuma query de construcao sai em qualquer um dos dois casos.
    deps = replace(
        deps,
        settings=deps.settings.model_copy(
            update={"api_points_floor": 9990.0, "cold_build_batch_safety_margin": 5.0}
        ),
    )
    with pytest.raises((CohortDeferredBudget, ImpossibleColdBuildPolicy)):
        build_cohorts(
            deps,
            encounter_id=ENCOUNTER_ID,
            class_name="Warlock",
            spec_name="Demonology",
            difficulty=5,
            duration_bucket_s=None,
        )
    assert transport.calls == []


def _criteria_for(duration_s: float, *, policy: str) -> CohortCriteria:
    bucket_lo, bucket_hi = duration_bucket_bounds(duration_bucket_id(duration_s))
    return CohortCriteria(
        encounter_id=ENCOUNTER_ID,
        difficulty=5,
        partition=3,
        class_name="Warlock",
        spec_name="Demonology",
        metric="dps",
        duration_min_s=bucket_lo,
        duration_max_s=bucket_hi,
        matching_policy_version=policy,
    )


def _seed_candidates(duration_s: float) -> list[RankingCandidate]:
    return [
        RankingCandidate(
            report_code=f"HIST{i:011d}"[:16],
            fight_id=1,
            player_name=f"Historical{i}",
            duration_s=duration_s,
        )
        for i in range(8)
    ]


def test_explicit_build_does_not_treat_matching_v1_pool_as_current(tmp_path: Path) -> None:
    duration_s = 100.0
    transport = _DispatchTransport(_responses_for({duration_s: 8}))
    deps = _build_deps(tmp_path, transport)
    v1_criteria = _criteria_for(duration_s, policy="v1")
    current_criteria = _criteria_for(duration_s, policy="v2")
    historical = _seed_candidates(duration_s)
    assert v1_criteria.cohort_id() != current_criteria.cohort_id()
    deps.store.write_candidate_pool(v1_criteria.cohort_id(), historical, criteria=v1_criteria)

    results = build_cohorts(
        deps,
        encounter_id=ENCOUNTER_ID,
        class_name="Warlock",
        spec_name="Demonology",
        difficulty=5,
        duration_bucket_s=duration_s,
    )

    assert [result.cohort_id for result in results] == [current_criteria.cohort_id()]
    assert deps.store.read_candidate_pool(v1_criteria.cohort_id()) == historical
    assert deps.store.read_candidate_pool(current_criteria.cohort_id()) is not None


def test_explicit_build_short_circuits_for_current_identity(tmp_path: Path) -> None:
    duration_s = 100.0
    transport = _DispatchTransport(_responses_for({duration_s: 8}))
    deps = _build_deps(tmp_path, transport)
    current_criteria = _criteria_for(duration_s, policy="v2")
    current = _seed_candidates(duration_s)
    deps.store.write_candidate_pool(
        current_criteria.cohort_id(), current, criteria=current_criteria
    )

    results = build_cohorts(
        deps,
        encounter_id=ENCOUNTER_ID,
        class_name="Warlock",
        spec_name="Demonology",
        difficulty=5,
        duration_bucket_s=duration_s,
    )

    assert [result.cohort_id for result in results] == [current_criteria.cohort_id()]
    assert results[0].n_members == len(current)
    assert transport.calls == ["partition"]


def _rankings_page(rankings: list[dict[str, Any]], *, has_more: bool = False) -> dict[str, Any]:
    return {
        "data": {
            "worldData": {
                "encounter": {"characterRankings": {"rankings": rankings, "hasMorePages": has_more}}
            }
        }
    }


def _ranking(name: str, duration_s: float, code: str) -> dict[str, Any]:
    return {"name": name, "duration": duration_s * 1000, "report": {"code": code, "fightID": 1}}


def _responses_for(bucket_specs: dict[float, int]) -> dict[str, Any]:
    """bucket_specs: {duration_s: n_candidates} — one ranking pool spanning
    however many distinct duration groups the test needs.
    """
    rankings: list[dict[str, Any]] = []
    meta: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    percentile: list[dict[str, Any]] = []

    player_id = 100
    for duration_s, n in bucket_specs.items():
        for i in range(n):
            code = f"C{int(duration_s)}{i:04d}0000000"[:16]
            rankings.append(_ranking(f"P{player_id}", duration_s, code))
            meta.append(
                _meta_response(
                    player_id=player_id,
                    player_name=f"P{player_id}",
                    start=0,
                    end=int(duration_s * 1000),
                    damage_total=900_000.0,
                )
            )
            events.append(_events_response([]))
            percentile.append(_percentile_response(None, code, 1))
            player_id += 1

    return {
        "meta": meta,
        "events": events,
        "percentile": percentile,
        "rankings": [_rankings_page(rankings)],
        "partition": _zone_partitions_response(),
    }


def test_builds_every_sufficiently_populated_bucket_when_no_bucket_given(tmp_path: Path) -> None:
    responses = _responses_for({100.0: 8, 500.0: 8})
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)

    results = build_cohorts(
        deps,
        encounter_id=ENCOUNTER_ID,
        class_name="Warlock",
        spec_name="Demonology",
        difficulty=5,
        duration_bucket_s=None,
    )

    assert len(results) == 2
    assert {r.n_members for r in results} == {8}


def test_skips_buckets_below_cohort_min_hard(tmp_path: Path) -> None:
    responses = _responses_for({100.0: 8, 500.0: 3})  # 3 < COHORT_MIN_HARD (8)
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)

    results = build_cohorts(
        deps,
        encounter_id=ENCOUNTER_ID,
        class_name="Warlock",
        spec_name="Demonology",
        difficulty=5,
        duration_bucket_s=None,
    )

    assert len(results) == 1
    assert results[0].n_members == 8


def test_explicit_duration_bucket_builds_only_that_bucket(tmp_path: Path) -> None:
    responses = _responses_for({100.0: 8, 500.0: 8})
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)

    results = build_cohorts(
        deps,
        encounter_id=ENCOUNTER_ID,
        class_name="Warlock",
        spec_name="Demonology",
        difficulty=5,
        duration_bucket_s=500.0,
    )

    assert len(results) == 1
    assert results[0].duration_min_s <= 500.0 < results[0].duration_max_s


def test_persisted_candidate_pool_is_readable_from_the_store(tmp_path: Path) -> None:
    """T2.1 (docs/architecture.md D-25): build_cohorts warms the candidate-pool
    cache, not an aggregated profile — matching happens per-player, in
    analysis/pipeline.py.
    """
    responses = _responses_for({100.0: 8})
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)

    results = build_cohorts(
        deps,
        encounter_id=ENCOUNTER_ID,
        class_name="Warlock",
        spec_name="Demonology",
        difficulty=5,
        duration_bucket_s=None,
    )

    pool = deps.store.read_candidate_pool(results[0].cohort_id)
    assert pool is not None
    assert len(pool) == 8


def test_rerunning_build_cohort_does_not_duplicate_log_rows(tmp_path: Path) -> None:
    """T1.7's own acceptance criterion: rodar build-cohort duas vezes não
    altera a contagem de linhas em `logs`.
    """
    responses = _responses_for({100.0: 8})
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)

    build_cohorts(
        deps,
        encounter_id=ENCOUNTER_ID,
        class_name="Warlock",
        spec_name="Demonology",
        difficulty=5,
        duration_bucket_s=None,
    )
    rows_after_first = deps.store.query("SELECT count(*) AS n FROM logs")["n"][0]

    # Re-fetch the SAME rankings response (still queued: "rankings" and
    # "partition" are single-item/dict, reused across calls; meta/events/
    # percentile for the 8 candidates were fully consumed already, but a
    # second run should hit the log cache and never need them again).
    same_rankings = [_ranking(f"P{100 + i}", 100.0, f"C100{i:04d}0000000"[:16]) for i in range(8)]
    transport._responses["rankings"] = [_rankings_page(same_rankings)]
    build_cohorts(
        deps,
        encounter_id=ENCOUNTER_ID,
        class_name="Warlock",
        spec_name="Demonology",
        difficulty=5,
        duration_bucket_s=None,
    )
    rows_after_second = deps.store.query("SELECT count(*) AS n FROM logs")["n"][0]

    assert rows_after_second == rows_after_first


def test_rate_limit_budget_exceeded_propagates_and_saves_partial_progress(tmp_path: Path) -> None:
    responses = _responses_for({100.0: 8, 500.0: 8})
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)

    original_query = cast("list[dict[str, Any]]", transport._responses["meta"])

    class _ExhaustingList(list):  # type: ignore[type-arg]
        """Raises budget-exceeded once the first bucket's candidates are
        exhausted, simulating the account running out mid-batch.
        """

        def pop(self, index: SupportsIndex = -1) -> Any:
            if not self:
                raise RateLimitBudgetExceeded(
                    "orçamento excedido", points_remaining=10.0, reset_in_seconds=60.0
                )
            return super().pop(index)

    transport._responses["meta"] = _ExhaustingList(original_query[:8])

    with pytest.raises(RateLimitBudgetExceeded):
        build_cohorts(
            deps,
            encounter_id=ENCOUNTER_ID,
            class_name="Warlock",
            spec_name="Demonology",
            difficulty=5,
            duration_bucket_s=None,
        )

    rows = deps.store.query("SELECT count(*) AS n FROM logs")["n"][0]
    assert rows == 8  # the first bucket's 8 reference logs stayed persisted
