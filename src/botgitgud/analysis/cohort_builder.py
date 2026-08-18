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

import structlog

from botgitgud.analysis.cohort import COHORT_MIN_HARD, duration_bucket_bounds, duration_bucket_id
from botgitgud.analysis.pipeline import Deps
from botgitgud.domain.models import CohortCriteria, RankingCandidate
from botgitgud.ingest.rankings import (
    fetch_cohort_logs,
    fetch_ranking_candidates,
    get_current_partition,
)

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BucketBuildResult:
    bucket_id: int
    duration_min_s: float
    duration_max_s: float
    n_members: int
    cohort_id: str


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

        # Warms the T1.4 per-log Store cache for every candidate in this
        # bucket — the value of a batch build. The result is discarded:
        # aggregation happens per-player, in analysis/pipeline.py, since
        # T2.1 (docs/desvios.md D-25).
        fetch_cohort_logs(deps.fetcher, bucket_candidates, max_workers=deps.settings.max_workers)

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
        deps.store.write_candidate_pool(cohort_id, bucket_candidates)
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
            )
        )

    return results
