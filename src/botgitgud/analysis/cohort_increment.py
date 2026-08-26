"""Construção incremental, checkpointada e resumível de uma coorte fria.

B1: exigir que 100 referências caibam numa única janela horária é impossível
numa conta de 3.600 pts/h — a política interativa pedia 3.804 pontos e o
sintoma aparecia como um `COHORT_DEFERRED_BUDGET` eterno. O prewarm já havia
resolvido isso avançando por janelas; este módulo extrai esse algoritmo para
que o caminho interativo/worker use exatamente o mesmo, em vez de uma segunda
implementação paralela.

Três propriedades sustentam o desenho:

- **cache como checkpoint**: `LogFetcher.fetch_many` consulta o Store antes de
  qualquer rede, então uma referência concluída numa janela anterior nunca é
  refetchada — o progresso não precisa de tabela própria;
- **parcial nunca é READY**: o pool só é escrito quando TODAS as referências
  planejadas estão em cache, então nenhuma análise consome uma coorte pela
  metade;
- **guarda em tempo de execução**: o preflight não basta. O orçamento real é
  remedido a cada lote, então um custo por referência acima do modelo encolhe
  o próximo lote em vez de furar o piso.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import structlog

from botgitgud.analysis.cold_build import (
    ColdBuildMode,
    affordable_references,
    estimate_cold_build,
    record_cold_lifecycle,
)
from botgitgud.config import Settings
from botgitgud.domain.models import CohortCriteria, RankingCandidate
from botgitgud.ingest.log_fetcher import LogFetcher
from botgitgud.ingest.rankings import fetch_cohort_logs
from botgitgud.ingest.store import Store

log = structlog.get_logger(__name__)


class CohortState(StrEnum):
    """Um pool parcial NUNCA e READY: a analise interativa nao pode usa-lo
    como se estivesse completo.
    """

    READY = "ready"
    DEFERRED_BUDGET = "deferred_budget"
    FAILED = "failed"


class DeferReason(StrEnum):
    """Por que a janela terminou sem completar a coorte."""

    BUDGET = "budget"
    # Um lote inteiro voltou sem nenhuma referencia nova em cache (tipicamente
    # logs que a WCL nao serve mais). Sem esta condicao o laco giraria para
    # sempre pedindo as mesmas referencias.
    NO_PROGRESS = "no_progress"


@dataclass(frozen=True, slots=True)
class CohortIncrement:
    """O que esta janela conseguiu avançar."""

    cohort_id: str
    state: CohortState
    planned: int
    completed: int
    fetched_this_window: int
    batches: int
    defer_reason: DeferReason | None = None

    @property
    def remaining(self) -> int:
        return self.planned - self.completed

    @property
    def is_ready(self) -> bool:
        return self.state is CohortState.READY


def _key(candidate: RankingCandidate) -> tuple[str, int, str]:
    return (candidate.report_code, candidate.fight_id, candidate.player_name)


def _pending(store: Store, candidates: list[RankingCandidate]) -> list[RankingCandidate]:
    return [
        c for c in candidates if store.read_log(c.report_code, c.fight_id, c.player_name) is None
    ]


def _available_points(client: object) -> float:
    refresh = getattr(client, "refresh_budget", None)
    if callable(refresh):
        refresh()
    points = getattr(client, "points_remaining", None)
    return float(points) if isinstance(points, int | float) else 0.0


def advance_cohort_build(
    *,
    client: object,
    fetcher: LogFetcher,
    store: Store,
    settings: Settings,
    cohort_id: str,
    criteria: CohortCriteria,
    candidates: list[RankingCandidate],
    partition: int | None,
    mode: ColdBuildMode,
    job_id: str | None = None,
) -> CohortIncrement:
    """Avança a coorte o quanto o orçamento desta janela permitir.

    Devolve READY somente quando as `candidates` inteiras estão em cache e o
    pool foi persistido. Caso contrário devolve DEFERRED_BUDGET com o progresso
    já preservado — o chamador decide se isso vira um exit code, uma exceção de
    domínio ou um job adiado.

    `RateLimitBudgetExceeded` continua propagando: é o backstop do próprio
    cliente ao piso da API, e o worker já sabe reenfileirar por causa dele.
    """
    planned = len(candidates)
    pending = _pending(store, candidates)
    completed_before = planned - len(pending)
    record_cold_lifecycle(
        "build_started",
        cohort_id,
        mode=str(mode),
        state=str(CohortState.DEFERRED_BUDGET) if pending else str(CohortState.READY),
        job_id=job_id,
        planned=planned,
        completed=completed_before,
        remaining=len(pending),
    )

    batches = 0
    defer_reason: DeferReason | None = None
    # Cada referência é tentada no máximo uma vez por janela. Sem isto, uma
    # referência que a WCL não serve mais voltaria a cada lote — gastando pontos
    # de novo — ou, se caísse toda no primeiro lote, encerraria a janela cedo e
    # bloquearia as referências boas que vinham depois.
    attempted: set[tuple[str, int, str]] = set()
    try:
        while pending:
            untried = [c for c in pending if _key(c) not in attempted]
            if not untried:
                defer_reason = DeferReason.NO_PROGRESS
                log.warning(
                    "cohort_increment.no_fetchable_reference_left",
                    cohort_id=cohort_id,
                    remaining=len(pending),
                )
                break
            available = _available_points(client)
            affordable = affordable_references(settings, available, mode=mode, planned=len(untried))
            if affordable <= 0:
                defer_reason = DeferReason.BUDGET
                break
            # O lote é o intervalo entre duas medições reais do orçamento: é ele
            # que limita o quanto o consumo pode passar do previsto antes da
            # próxima reavaliação.
            size = min(affordable, settings.cold_build_chunk_references, len(untried))
            chunk = untried[:size]
            record_cold_lifecycle(
                "building",
                cohort_id,
                mode=str(mode),
                state="building",
                job_id=job_id,
                planned=planned,
                completed=planned - len(pending),
                remaining=len(pending),
                batch_size=size,
                current_budget=available,
                estimated_points_remaining=estimate_cold_build(
                    settings, available, mode=mode, references=len(pending)
                ).estimated_upper_bound,
            )
            fetch_cohort_logs(
                fetcher,
                chunk,
                max_workers=settings.max_workers,
                expected_partition=partition,
            )
            batches += 1
            attempted.update(_key(c) for c in chunk)
            landed = {_key(c) for c in chunk} - {_key(c) for c in _pending(store, chunk)}
            pending = [c for c in pending if _key(c) not in landed]
    except Exception as exc:
        record_cold_lifecycle(
            "build_failed",
            cohort_id,
            mode=str(mode),
            state=str(CohortState.FAILED),
            job_id=job_id,
            planned=planned,
            completed=planned - len(pending),
            reason=type(exc).__name__,
        )
        raise

    completed = planned - len(pending)
    fetched = completed - completed_before
    if pending:
        record_cold_lifecycle(
            "deferred_budget",
            cohort_id,
            mode=str(mode),
            state=str(CohortState.DEFERRED_BUDGET),
            job_id=job_id,
            planned=planned,
            completed=completed,
            remaining=len(pending),
            reason=str(defer_reason) if defer_reason else None,
            current_budget=_last_known_points(client),
            estimated_points_remaining=estimate_cold_build(
                settings, 0.0, mode=mode, references=len(pending)
            ).estimated_upper_bound,
        )
        log.warning(
            "cohort_increment.deferred",
            cohort_id=cohort_id,
            mode=str(mode),
            planned=planned,
            completed=completed,
            fetched_this_window=fetched,
            reason=str(defer_reason) if defer_reason else None,
        )
        return CohortIncrement(
            cohort_id=cohort_id,
            state=CohortState.DEFERRED_BUDGET,
            planned=planned,
            completed=completed,
            fetched_this_window=fetched,
            batches=batches,
            defer_reason=defer_reason,
        )

    store.write_candidate_pool(cohort_id, candidates, criteria=criteria)
    record_cold_lifecycle(
        "build_completed",
        cohort_id,
        mode=str(mode),
        state=str(CohortState.READY),
        job_id=job_id,
        planned=planned,
        completed=planned,
        remaining=0,
        n_members=planned,
        resumed_from=completed_before,
    )
    log.info(
        "cohort_increment.ready",
        cohort_id=cohort_id,
        mode=str(mode),
        n_members=planned,
        resumed_from=completed_before,
        fetched_this_window=fetched,
    )
    return CohortIncrement(
        cohort_id=cohort_id,
        state=CohortState.READY,
        planned=planned,
        completed=planned,
        fetched_this_window=fetched,
        batches=batches,
    )


def _last_known_points(client: object) -> float | None:
    points = getattr(client, "points_remaining", None)
    return float(points) if isinstance(points, int | float) else None
