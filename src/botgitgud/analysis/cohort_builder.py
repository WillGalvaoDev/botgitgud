"""T1.7 — batch cohort-building, the logic behind the CLI's `build-cohort`
subcommand: "Baixa rankings, ingere os logs (usando o cache da T1.4)."

Duration buckets (analysis/cohort.py, 5% wide) are discovered from the
live pool in a single unfiltered rankings fetch (docs/desvios.md D-15's
`target_duration_s=None` mode), then built and persisted one at a time —
"salva o progresso parcial" if RateLimitBudgetExceeded fires mid-batch,
since each bucket is persisted immediately after it's built, not batched
to the end.

T2.1 (docs/desvios.md D-25): this no longer aggregates a CohortProfile —
per-player covariate matching means there is no single "the" profile for
a bucket, only a candidate pool that every player's own analysis matches
against independently (analysis/pipeline.py). This job's value is
prefetching: it warms the candidate-pool cache (Store.write_candidate_pool)
AND the individual reference-log cache (fetch_cohort_logs, via LogFetcher's
T1.4 Store-backed cache) so the next interactive `!analisar` for this
bucket never needs a synchronous rankings query or a synchronous 100-log
fetch.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

import structlog

from botgitgud.analysis.cohort import COHORT_MIN_HARD, duration_bucket_bounds, duration_bucket_id
from botgitgud.analysis.cold_build import (
    ColdBuildMode,
    affordable_references,
    preflight_cold_build,
    record_cold_lifecycle,
)
from botgitgud.analysis.pipeline import Deps
from botgitgud.domain.models import CohortCriteria, RankingCandidate
from botgitgud.ingest.rankings import (
    fetch_cohort_logs,
    fetch_ranking_candidates,
    get_current_partition,
)

log = structlog.get_logger(__name__)


class CohortState(StrEnum):
    """Um pool parcial NUNCA e READY: a analise interativa nao pode usa-lo
    como se estivesse completo.
    """

    READY = "ready"
    DEFERRED_BUDGET = "deferred_budget"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class BucketBuildResult:
    bucket_id: int
    duration_min_s: float
    duration_max_s: float
    n_members: int
    cohort_id: str
    state: CohortState = CohortState.READY
    planned: int = 0
    completed: int = 0

    @property
    def is_ready(self) -> bool:
        return self.state is CohortState.READY


def build_cohorts(
    deps: Deps,
    *,
    encounter_id: int,
    class_name: str,
    spec_name: str,
    difficulty: int,
    duration_bucket_s: float | None,
) -> list[BucketBuildResult]:
    """Warms the candidate-pool cache and the individual log cache for
    every duration bucket with at least COHORT_MIN_HARD candidates — or
    just the one containing `duration_bucket_s` when given. Raises
    RateLimitBudgetExceeded if the budget runs out mid-batch.
    """
    # An explicit READY bucket is a pure cache hit: do not spend even a
    # budget refresh merely to discover that no rebuild is needed.
    if duration_bucket_s is not None:
        requested_bucket = duration_bucket_id(duration_bucket_s)
        for ready in deps.store.list_ready_cohorts():
            if (
                ready["encounter_id"] == encounter_id
                and ready["difficulty"] == difficulty
                and ready["class_name"] == class_name
                and ready["spec_name"] == spec_name
                and duration_bucket_id(cast(float, ready["duration_min_s"])) == requested_bucket
            ):
                return [
                    BucketBuildResult(
                        bucket_id=requested_bucket,
                        duration_min_s=cast(float, ready["duration_min_s"]),
                        duration_max_s=cast(float, ready["duration_max_s"]),
                        n_members=cast(int, ready["n_members"]),
                        cohort_id=str(ready["cohort_id"]),
                    )
                ]

    # The batch identity is refined to canonical cohort_ids after partition
    # and duration buckets are known. This gate intentionally precedes even
    # those discovery calls: a deferred batch makes zero construction calls.
    # Prewarm usa a politica de batch (piso da API, sem reserva interativa) e
    # avanca incrementalmente: exigir que ~100 logs caibam numa unica janela
    # horaria excede o proprio teto da conta — ver docs/production-readiness-
    # cold-build.md.
    preflight_cold_build(
        deps.client,
        deps.settings,
        f"prewarm:{encounter_id}:{difficulty}:{class_name}:{spec_name}:{duration_bucket_s}",
        mode=ColdBuildMode.PREWARM,
        references=1,
    )
    partition = get_current_partition(deps.client, encounter_id)
    candidates = fetch_ranking_candidates(
        deps.client,
        encounter_id=encounter_id,
        class_name=class_name,
        spec_name=spec_name,
        partition=partition,
        target_duration_s=None,
    )

    by_bucket: dict[int, list[RankingCandidate]] = defaultdict(list)
    for c in candidates:
        by_bucket[duration_bucket_id(c.duration_s)].append(c)

    if duration_bucket_s is not None:
        target_id = duration_bucket_id(duration_bucket_s)
        by_bucket = {target_id: by_bucket.get(target_id, [])}

    results: list[BucketBuildResult] = []
    for bucket_id, bucket_candidates in sorted(by_bucket.items()):
        if len(bucket_candidates) < COHORT_MIN_HARD:
            log.warning(
                "cohort_builder.bucket_skipped",
                bucket_id=bucket_id,
                n_candidates=len(bucket_candidates),
            )
            continue

        bucket_lo, bucket_hi = duration_bucket_bounds(bucket_id)
        criteria = CohortCriteria(
            encounter_id=encounter_id,
            difficulty=difficulty,
            partition=partition,
            class_name=class_name,
            spec_name=spec_name,
            metric="dps",
            duration_min_s=bucket_lo,
            duration_max_s=bucket_hi,
        )
        cohort_id = criteria.cohort_id()
        existing = deps.store.read_candidate_pool(cohort_id)
        if existing is not None:
            log.info("cohort_builder.bucket_already_ready", cohort_id=cohort_id)
            results.append(
                BucketBuildResult(
                    bucket_id=bucket_id,
                    duration_min_s=bucket_lo,
                    duration_max_s=bucket_hi,
                    n_members=len(existing),
                    cohort_id=cohort_id,
                )
            )
            continue
        # Reference logs inherit the criteria partition. This avoids the
        # redundant per-report rankings query without changing analyzed logs.
        record_cold_lifecycle("build_started", cohort_id, planned=len(bucket_candidates))
        # Resume sem refetch: `fetch_many` ja consulta o cache de logs antes de
        # qualquer rede, entao as referencias concluidas numa janela anterior
        # nao voltam a custar pontos.
        pending = [
            c
            for c in bucket_candidates
            if deps.store.read_log(c.report_code, c.fight_id, c.player_name) is None
        ]
        completed_before = len(bucket_candidates) - len(pending)
        affordable = affordable_references(
            deps.settings,
            deps.client.points_remaining or 0.0,
            mode=ColdBuildMode.PREWARM,
            planned=len(pending),
        )
        try:
            if affordable:
                fetch_cohort_logs(
                    deps.fetcher,
                    pending[:affordable],
                    max_workers=deps.settings.max_workers,
                    expected_partition=partition,
                )
        except Exception as exc:
            record_cold_lifecycle("build_failed", cohort_id, reason=type(exc).__name__)
            raise

        still_pending = [
            c
            for c in bucket_candidates
            if deps.store.read_log(c.report_code, c.fight_id, c.player_name) is None
        ]
        completed = len(bucket_candidates) - len(still_pending)
        if still_pending:
            # Progresso preservado nos logs individuais; o POOL so e escrito
            # quando tudo esta pronto, entao a coorte nao vira READY parcial.
            record_cold_lifecycle(
                "deferred_budget",
                cohort_id,
                planned=len(bucket_candidates),
                completed=completed,
                remaining=len(still_pending),
            )
            log.warning(
                "cohort_builder.bucket_deferred_budget",
                cohort_id=cohort_id,
                completed=completed,
                planned=len(bucket_candidates),
            )
            results.append(
                BucketBuildResult(
                    bucket_id=bucket_id,
                    duration_min_s=bucket_lo,
                    duration_max_s=bucket_hi,
                    n_members=completed,
                    cohort_id=cohort_id,
                    state=CohortState.DEFERRED_BUDGET,
                    planned=len(bucket_candidates),
                    completed=completed,
                )
            )
            continue

        deps.store.write_candidate_pool(cohort_id, bucket_candidates, criteria=criteria)
        record_cold_lifecycle(
            "build_completed",
            cohort_id,
            n_members=len(bucket_candidates),
            resumed_from=completed_before,
        )
        log.info(
            "cohort_builder.bucket_built",
            bucket_id=bucket_id,
            cohort_id=cohort_id,
            n_candidates=len(bucket_candidates),
        )
        results.append(
            BucketBuildResult(
                bucket_id=bucket_id,
                duration_min_s=bucket_lo,
                duration_max_s=bucket_hi,
                n_members=len(bucket_candidates),
                cohort_id=cohort_id,
                state=CohortState.READY,
                planned=len(bucket_candidates),
                completed=len(bucket_candidates),
            )
        )

    return results
