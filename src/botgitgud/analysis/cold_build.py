"""Production-safety policy for expensive cold cohort construction."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
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

    @property
    def points_limit(self) -> float | None: ...

    def refresh_budget(self) -> None: ...


class ColdBuildMode(StrEnum):
    """As duas politicas nao podem ser identicas por acidente.

    INTERACTIVE nasce de um `!analisar`: o caminho quente vale mais do que a
    conveniencia de construir agora, entao a reserva interativa e protegida
    inteira e o build e adiado sem cerimonia.

    PREWARM e o operador rodando `build-cohort` de proposito para preparar a
    coorte. Ele ainda respeita o piso da API, mas nao precisa preservar a
    reserva interativa — preserva-la aqui e o que tornava o prewarm impossivel.
    """

    INTERACTIVE = "interactive"
    PREWARM = "prewarm"


@dataclass(frozen=True, slots=True)
class ColdBuildCost:
    """Custo estimado com banda explicita — o preflight nao finge precisao.

    `estimated_api_points` e a estimativa esperada (constante medida);
    `estimated_upper_bound` e a conservadora, usada para decidir.
    """

    estimated_api_points: float
    estimated_upper_bound: float
    available_api_points: float
    protected_floor: float
    safety_margin: float
    mode: ColdBuildMode
    references: int

    @property
    def projected_remaining(self) -> float:
        return self.available_api_points - self.estimated_upper_bound

    @property
    def required_to_start(self) -> float:
        return self.estimated_upper_bound + self.protected_floor + self.safety_margin

    @property
    def allowed(self) -> bool:
        return self.projected_remaining >= self.protected_floor + self.safety_margin


def _protected_floor(settings: Settings, mode: ColdBuildMode) -> float:
    if mode is ColdBuildMode.PREWARM:
        return settings.api_points_floor
    return max(settings.api_points_floor, settings.hot_path_reserve)


def _safety_margin(settings: Settings, mode: ColdBuildMode) -> float:
    if mode is ColdBuildMode.PREWARM:
        return settings.cold_build_batch_safety_margin
    return settings.cold_build_safety_margin


def _upper_points_per_query(settings: Settings) -> float:
    return settings.cold_build_points_per_query * settings.cold_build_cost_uncertainty


def queries_for(settings: Settings, references: int) -> int:
    per_ref = references * settings.cold_build_queries_per_reference
    return settings.cold_build_fixed_queries + per_ref


def estimate_cold_build(
    settings: Settings,
    available: float,
    *,
    mode: ColdBuildMode = ColdBuildMode.INTERACTIVE,
    references: int | None = None,
) -> ColdBuildCost:
    refs = settings.cohort_max if references is None else references
    queries = queries_for(settings, refs)
    return ColdBuildCost(
        estimated_api_points=queries * settings.cold_build_points_per_query,
        estimated_upper_bound=queries * _upper_points_per_query(settings),
        available_api_points=available,
        protected_floor=_protected_floor(settings, mode),
        safety_margin=_safety_margin(settings, mode),
        mode=mode,
        references=refs,
    )


def affordable_references(
    settings: Settings, available: float, *, mode: ColdBuildMode, planned: int
) -> int:
    """Quantas referencias cabem AGORA sem furar piso + margem.

    E isto que torna o prewarm incremental possivel: em vez de exigir que 100
    logs caibam numa unica janela horaria — o que excede o proprio teto da
    conta —, processa-se o quanto couber, faz-se checkpoint, e o resto segue
    na janela seguinte.
    """
    budget = available - _protected_floor(settings, mode) - _safety_margin(settings, mode)
    fixed = settings.cold_build_fixed_queries * _upper_points_per_query(settings)
    per_ref = settings.cold_build_queries_per_reference * _upper_points_per_query(settings)
    if budget <= fixed or per_ref <= 0:
        return 0
    return max(0, min(planned, int((budget - fixed) // per_ref)))


class ImpossibleColdBuildPolicy(RuntimeError):
    """A configuracao exige mais pontos do que a conta pode ter. Sem isto, o
    sintoma aparece como um `COHORT_DEFERRED_BUDGET` eterno — que foi
    exatamente o bug desta correcao.
    """


def validate_cold_build_policy(settings: Settings, limit_per_hour: float) -> None:
    """Detecta politica matematicamente impossivel na configuracao.

    Avalia o modo PREWARM com UMA referencia: se nem o menor incremento
    possivel cabe no teto da conta, nenhuma construcao jamais avanca e o
    operador precisa saber disso como erro de configuracao.
    """
    minimal = estimate_cold_build(
        settings, limit_per_hour, mode=ColdBuildMode.PREWARM, references=1
    )
    if minimal.required_to_start > limit_per_hour:
        raise ImpossibleColdBuildPolicy(
            f"politica de budget impossivel: iniciar o menor prewarm possivel exige "
            f"{minimal.required_to_start:.0f} pontos, mas o teto da conta e "
            f"{limit_per_hour:.0f}. Reduza api_points_floor/margem ou o custo por referencia."
        )


def preflight_cold_build(
    client: BudgetClient,
    settings: Settings,
    cohort_id: str,
    *,
    mode: ColdBuildMode = ColdBuildMode.INTERACTIVE,
    references: int | None = None,
) -> ColdBuildCost:
    record_cold_lifecycle("preflight", cohort_id, mode=str(mode))
    client.refresh_budget()
    available = client.points_remaining
    cost = estimate_cold_build(
        settings, 0.0 if available is None else available, mode=mode, references=references
    )
    # Diagnose a impossibilidade AQUI, onde o sintoma aparecia: sem isto, uma
    # politica que nao cabe no teto da conta se disfarca de defer eterno. So e
    # verificavel depois do refresh, que e quem revela o teto.
    if client.points_limit is not None:
        validate_cold_build_policy(settings, client.points_limit)
    if not cost.allowed:
        record_cold_lifecycle(
            "deferred",
            cohort_id,
            mode=str(mode),
            estimated_cost=cost.estimated_api_points,
            estimated_upper_bound=cost.estimated_upper_bound,
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
        mode=str(mode),
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
