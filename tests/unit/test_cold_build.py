from __future__ import annotations

import threading
import time

import pytest

from botgitgud.analysis.cold_build import (
    CohortSingleFlight,
    estimate_cold_build,
    preflight_cold_build,
)
from botgitgud.analysis.cold_build_benchmark import benchmark
from botgitgud.bot.job_models import BudgetStatus
from botgitgud.config import Settings
from botgitgud.errors import CohortDeferredBudget, InsufficientCohort


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "discord_token": "x",
        "wcl_client_id": "x",
        "wcl_client_secret": "x",
        "blizzard_client_id": "x",
        "blizzard_client_secret": "x",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type,call-arg]


class BudgetClient:
    def __init__(self, points: float) -> None:
        self.points_remaining = points
        self.refreshes = 0

    def refresh_budget(self) -> None:
        self.refreshes += 1


def test_preflight_allows_only_when_projected_budget_preserves_floor_and_margin() -> None:
    cfg = settings(cold_build_points_per_query=1.0)
    estimate = estimate_cold_build(cfg, available=3000.0)
    assert estimate.estimated_api_points == 1506.0
    assert estimate.projected_remaining == 1494.0
    assert estimate.allowed
    assert preflight_cold_build(BudgetClient(3000), cfg, "cohort-a").allowed


def test_preflight_defers_at_boundary_and_is_a_distinct_domain_state() -> None:
    cfg = settings(cold_build_points_per_query=1.0)
    client = BudgetClient(2755.0)  # projected=1249, required floor+margin=1250
    with pytest.raises(CohortDeferredBudget) as caught:
        preflight_cold_build(client, cfg, "cohort-a")
    assert client.refreshes == 1
    assert caught.value.protected_floor == 1000.0
    assert not isinstance(caught.value, InsufficientCohort)
    assert "temporariamente reservado" in str(caught.value)


def test_hot_job_remains_allowed_when_the_same_budget_defers_cold() -> None:
    cfg = settings(cold_build_points_per_query=1.0)
    with pytest.raises(CohortDeferredBudget):
        preflight_cold_build(BudgetClient(2000), cfg, "cold")
    budget = BudgetStatus(points_remaining=2000, limit_per_hour=10000, floor=1000)
    assert budget.allows("analyze")


def test_single_flight_same_key_runs_one_leader_and_different_keys_do_not_mix() -> None:
    flights = CohortSingleFlight()
    leaders: list[tuple[str, bool]] = []
    barrier = threading.Barrier(3)

    def run(key: str) -> None:
        barrier.wait()
        with flights.acquire(key) as leader:
            leaders.append((key, leader))
            if leader:
                time.sleep(0.03)

    threads = [threading.Thread(target=run, args=(key,)) for key in ("a", "a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(leader for key, leader in leaders if key == "a") == 1
    assert sum(leader for key, leader in leaders if key == "b") == 1


def test_single_flight_releases_after_failure_for_later_retry() -> None:
    flights = CohortSingleFlight()
    with pytest.raises(RuntimeError), flights.acquire("a") as leader:
        assert leader
        raise RuntimeError("boom")
    with flights.acquire("a") as retry_leader:
        assert retry_leader


def test_offline_benchmark_is_explicitly_separate_from_real_smoke() -> None:
    result = benchmark()
    assert result["real_smoke_baseline"] == 1504
    assert result["offline_before"] == 1504
    assert result["offline_after"] == 1309
    assert result["reduction_pct"] == 12.97
