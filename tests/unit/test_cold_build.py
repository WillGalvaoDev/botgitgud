from __future__ import annotations

import threading
import time

import pytest

from botgitgud.analysis.cold_build import (
    CohortSingleFlight,
    ColdBuildExecution,
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
    def __init__(self, points: float, limit: float | None = 3600.0) -> None:
        self.points_remaining = points
        # O preflight usa o teto para distinguir "adiado agora" de "politica
        # impossivel por configuracao".
        self.points_limit = limit
        self.refreshes = 0

    def refresh_budget(self) -> None:
        self.refreshes += 1


def test_preflight_allows_only_when_projected_budget_preserves_floor_and_margin() -> None:
    # Banda de PONTOS degenerada (1.0) de proposito: isola a banda de QUERIES,
    # que e a variavel falsificada pelo prewarm real (23,4 q/ref medidas contra
    # 15 modeladas). As duas bandas sao independentes por desenho.
    cfg = settings(cold_build_points_per_query=1.0, cold_build_cost_uncertainty=1.0)
    estimate = estimate_cold_build(cfg, available=5000.0)
    assert estimate.estimated_queries == pytest.approx(2346.0)  # 6 fixas + 100 * 23,4
    assert estimate.estimated_queries_upper == pytest.approx(3049.8)  # * 1,3
    assert estimate.estimated_api_points == pytest.approx(2346.0)
    # Quem decide e sempre o limite SUPERIOR, nunca o esperado.
    assert estimate.projected_remaining == pytest.approx(1950.2)
    assert estimate.allowed
    assert preflight_cold_build(BudgetClient(5000, limit=6000.0), cfg, "cohort-a").allowed


def test_preflight_defers_at_boundary_and_is_a_distinct_domain_state() -> None:
    cfg = settings(cold_build_points_per_query=1.0, cold_build_cost_uncertainty=1.0)
    # projected = 4299 - 3049,8 = 1249,2; exigido = floor 1000 + margem 250.
    client = BudgetClient(4299.0, limit=6000.0)
    with pytest.raises(CohortDeferredBudget) as caught:
        preflight_cold_build(client, cfg, "cohort-a")
    assert client.refreshes == 1
    assert caught.value.protected_floor == 1000.0
    assert not isinstance(caught.value, InsufficientCohort)
    assert "temporariamente reservado" in str(caught.value)


def test_hot_job_remains_allowed_when_the_same_budget_defers_cold() -> None:
    """A reserva quente vence a conveniencia de construir agora — mesmo no
    menor incremento resumivel possivel.
    """
    cfg = settings(cold_build_points_per_query=1.0)
    with pytest.raises(CohortDeferredBudget):
        preflight_cold_build(
            BudgetClient(1200, limit=3600.0),
            cfg,
            "cold",
            references=1,
            execution=ColdBuildExecution.RESUMABLE_INCREMENTAL,
        )
    budget = BudgetStatus(points_remaining=1200, limit_per_hour=10000, floor=1000)
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
