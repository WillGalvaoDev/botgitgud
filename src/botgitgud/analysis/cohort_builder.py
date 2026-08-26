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
from typing import cast

import structlog

from botgitgud.analysis.cohort import COHORT_MIN_HARD, duration_bucket_bounds, duration_bucket_id
from botgitgud.analysis.cohort_increment import CohortState, advance_cohort_build
from botgitgud.analysis.cold_build import (
    ColdBuildExecution,
    ColdBuildMode,
    preflight_cold_build,
)
from botgitgud.analysis.pipeline import Deps
from botgitgud.domain.models import CohortCriteria, RankingCandidate
from botgitgud.ingest.rankings import fetch_ranking_candidates, get_current_partition

log = structlog.get_logger(__name__)

__all__ = ["BucketBuildResult", "CohortState", "build_cohorts"]


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
        execution=ColdBuildExecution.RESUMABLE_INCREMENTAL,
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
        # O algoritmo incremental (cache-as-checkpoint, lote guardado pelo
        # orcamento real, parcial nunca READY) vive em cohort_increment.py e e
        # o MESMO usado pelo caminho interativo — ver docs/production-
        # readiness-cold-build.md.
        increment = advance_cohort_build(
            client=deps.client,
            fetcher=deps.fetcher,
            store=deps.store,
            settings=deps.settings,
            cohort_id=cohort_id,
            criteria=criteria,
            candidates=bucket_candidates,
            partition=partition,
            mode=ColdBuildMode.PREWARM,
        )
        results.append(
            BucketBuildResult(
                bucket_id=bucket_id,
                duration_min_s=bucket_lo,
                duration_max_s=bucket_hi,
                n_members=increment.completed,
                cohort_id=cohort_id,
                state=increment.state,
                planned=increment.planned,
                completed=increment.completed,
            )
        )

    return results
