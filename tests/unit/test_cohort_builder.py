"""T1.7 — tests for the batch cohort-building logic behind `build-cohort`.
Reuses test_pipeline.py's dispatch transport/response builders (same
fake-httpx-transport pattern as test_log_fetcher.py/test_pipeline.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, SupportsIndex, cast

import pytest
from test_log_fetcher import _events_response, _meta_response, _percentile_response
from test_pipeline import _build_deps, _DispatchTransport, _zone_partitions_response

from botgitgud.analysis.cohort_builder import build_cohorts
from botgitgud.errors import RateLimitBudgetExceeded

ENCOUNTER_ID = 3179


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


def test_persisted_profile_is_readable_from_the_store(tmp_path: Path) -> None:
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

    profile = deps.store.read_profile(results[0].cohort_id)
    assert profile is not None
    assert profile.n_members == 8


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
