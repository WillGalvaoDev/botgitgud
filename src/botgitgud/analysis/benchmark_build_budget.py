"""EB.4 — cost model + runtime budget guard for the Encounter Benchmark
builder. Deliberately NOT `analysis/cold_build.py`'s `ColdBuildMode`/
`ColdBuildCost`: those are calibrated to a full `PlayerLog` fetch
(~23.4 measured queries/reference) and their floor policy answers "does
this compete with a live `!analisar`?" — a question with exactly two
answers (INTERACTIVE/PREWARM). Coupling a third, structurally different
workload (fights, not references; no measured constant yet; lowest
priority in the system, always) onto that enum would blur what `mode`
even means there. This module mirrors `cold_build.py`'s SHAPE (frozen cost
dataclass, `queries_for`/`affordable_*`, `BudgetClient` reuse) without its
identity.

Every constant here is `config.py`'s `benchmark_build_*` fields, all
explicitly marked measured=false in their own docstring — this workload has
no live measurement yet, only the structural fact that a benchmark fight
costs at most 2 queries (one `report.rankings`, one `QUERY_PLAYER_SETUP_ONLY`)
regardless of how many candidates share it.
"""

from __future__ import annotations

from dataclasses import dataclass

from botgitgud.config import Settings


@dataclass(frozen=True, slots=True)
class BenchmarkBuildCost:
    """Same two-separate-bands shape as `ColdBuildCost` (queries vs points,
    each with its own uncertainty factor) — for the same reason: an
    unmeasured workload must never let a single fudge factor hide which
    side of the estimate is actually wrong once real data exists.
    """

    estimated_queries: float
    estimated_queries_upper: float
    estimated_api_points: float
    estimated_upper_bound: float
    available_api_points: float
    protected_floor: float
    safety_margin: float
    fights: int

    @property
    def projected_remaining(self) -> float:
        return self.available_api_points - self.estimated_upper_bound

    @property
    def required_to_start(self) -> float:
        return self.estimated_upper_bound + self.protected_floor + self.safety_margin

    @property
    def allowed(self) -> bool:
        return self.projected_remaining >= self.protected_floor + self.safety_margin


def protected_floor(settings: Settings) -> float:
    """Piso SOMADO (não `max`, como `cold_build._protected_floor` faz para
    INTERACTIVE) — estritamente mais conservador que qualquer
    `ColdBuildMode` existente: um benchmark nunca deve tocar nem a reserva
    interativa nem o piso bruto da API, os dois ao mesmo tempo.
    """
    return settings.api_points_floor + settings.hot_path_reserve


def _upper_points_per_query(settings: Settings) -> float:
    return settings.benchmark_build_points_per_query * settings.benchmark_build_cost_uncertainty


def queries_for(settings: Settings, fights: int) -> float:
    return (
        settings.benchmark_build_fixed_queries + fights * settings.benchmark_build_queries_per_fight
    )


def queries_upper_for(settings: Settings, fights: int) -> float:
    return queries_for(settings, fights) * settings.benchmark_build_cost_uncertainty


def points_per_fight_upper(settings: Settings) -> float:
    return (
        settings.benchmark_build_queries_per_fight
        * settings.benchmark_build_cost_uncertainty
        * _upper_points_per_query(settings)
    )


def estimate_benchmark_build(
    settings: Settings, available: float, *, fights: int
) -> BenchmarkBuildCost:
    queries = queries_for(settings, fights)
    queries_upper = queries_upper_for(settings, fights)
    return BenchmarkBuildCost(
        estimated_queries=queries,
        estimated_queries_upper=queries_upper,
        estimated_api_points=queries * settings.benchmark_build_points_per_query,
        estimated_upper_bound=queries_upper * _upper_points_per_query(settings),
        available_api_points=available,
        protected_floor=protected_floor(settings),
        safety_margin=settings.benchmark_build_safety_margin,
        fights=fights,
    )


def affordable_fights(settings: Settings, available: float, *, planned: int) -> int:
    """Quantos fights únicos cabem AGORA sem furar piso + margem — mesmo
    papel de `cold_build.affordable_references`, reavaliado a cada lote
    (guarda em tempo de execução: o preflight sozinho nunca basta).
    """
    budget = available - protected_floor(settings) - settings.benchmark_build_safety_margin
    fixed = (
        settings.benchmark_build_fixed_queries
        * settings.benchmark_build_cost_uncertainty
        * _upper_points_per_query(settings)
    )
    per_fight = points_per_fight_upper(settings)
    if budget <= fixed or per_fight <= 0:
        return 0
    return max(0, min(planned, int((budget - fixed) // per_fight)))
