"""Production-safety policy for expensive cold cohort construction."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol

from botgitgud.config import Settings
from botgitgud.errors import CohortDeferredBudget

_lifecycle_lock = threading.Lock()
_cold_lifecycle: dict[str, object] | None = None


def record_cold_lifecycle(stage: str, cohort_id: str, **details: object) -> None:
    global _cold_lifecycle
    with _lifecycle_lock:
        _cold_lifecycle = {
            "stage": stage,
            "cohort_id": cohort_id,
            "timestamp": time.time(),
            **details,
        }


def cold_lifecycle_snapshot() -> dict[str, object] | None:
    with _lifecycle_lock:
        return None if _cold_lifecycle is None else dict(_cold_lifecycle)


class BudgetClient(Protocol):
    @property
    def points_remaining(self) -> float | None: ...

    def refresh_budget(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ColdBuildCost:
    estimated_api_points: float
    available_api_points: float
    protected_floor: float
    safety_margin: float

    @property
    def projected_remaining(self) -> float:
        return self.available_api_points - self.estimated_api_points

    @property
    def allowed(self) -> bool:
        return self.projected_remaining >= self.protected_floor + self.safety_margin


def estimate_cold_build(settings: Settings, available: float) -> ColdBuildCost:
    queries = settings.cold_build_fixed_queries + (
        settings.cohort_max * settings.cold_build_queries_per_reference
    )
    return ColdBuildCost(
        estimated_api_points=queries * settings.cold_build_points_per_query,
        available_api_points=available,
        protected_floor=max(settings.api_points_floor, settings.hot_path_reserve),
        safety_margin=settings.cold_build_safety_margin,
    )


def preflight_cold_build(client: BudgetClient, settings: Settings, cohort_id: str) -> ColdBuildCost:
    record_cold_lifecycle("preflight", cohort_id)
    client.refresh_budget()
    available = client.points_remaining
    cost = estimate_cold_build(settings, 0.0 if available is None else available)
    if not cost.allowed:
        record_cold_lifecycle(
            "deferred",
            cohort_id,
            estimated_cost=cost.estimated_api_points,
            budget_before=cost.available_api_points,
            protected_floor=cost.protected_floor,
            reason="protected_floor_and_safety_margin",
        )
        raise CohortDeferredBudget(
            "Essa análise precisa preparar uma nova coorte e o orçamento da Warcraft Logs "
            "está temporariamente reservado. Tente novamente mais tarde.",
            cohort_id=cohort_id,
            estimated_api_points=cost.estimated_api_points,
            available_api_points=cost.available_api_points,
            protected_floor=cost.protected_floor,
            safety_margin=cost.safety_margin,
        )
    record_cold_lifecycle(
        "allowed",
        cohort_id,
        estimated_cost=cost.estimated_api_points,
        budget_before=cost.available_api_points,
        protected_floor=cost.protected_floor,
        reason="budget_safe",
    )
    return cost


class CohortSingleFlight:
    """Process-local single-flight keyed by canonical cohort_id."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active: set[str] = set()

    @contextmanager
    def acquire(self, cohort_id: str) -> Iterator[bool]:
        with self._condition:
            leader = cohort_id not in self._active
            if leader:
                self._active.add(cohort_id)
            else:
                while cohort_id in self._active:
                    self._condition.wait()
        try:
            yield leader
        finally:
            if leader:
                with self._condition:
                    self._active.remove(cohort_id)
                    self._condition.notify_all()


cohort_single_flight = CohortSingleFlight()
