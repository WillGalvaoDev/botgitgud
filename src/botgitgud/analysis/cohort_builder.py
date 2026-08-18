"""T1.7 — batch cohort-building, the logic behind the CLI's `build-cohort`
subcommand: "Baixa rankings, ingere os logs (usando o cache da T1.4),
calcula o CohortProfile e o persiste."

Duration buckets (analysis/cohort.py, 5% wide) are discovered from the
live pool in a single unfiltered rankings fetch (docs/desvios.md D-15's
`target_duration_s=None` mode), then built and persisted one at a time —
"salva o progresso parcial" if RateLimitBudgetExceeded fires mid-batch,
since each bucket is persisted immediately after it's built, not batched
to the end.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from botgitgud.analysis.cohort import COHORT_MIN_HARD, duration_bucket_bounds, duration_bucket_id
from botgitgud.analysis.pipeline import Deps
from botgitgud.analysis.profile import build_cd_reference_profile
from botgitgud.domain.models import CohortCriteria, CohortProfile
from botgitgud.ingest.rankings import (
    RankingCandidate,
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
    """Builds/persists a CohortProfile per duration bucket with at least
    COHORT_MIN_HARD candidates — every such bucket, or just the one
    containing `duration_bucket_s` when given. Raises
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

        reference_logs = fetch_cohort_logs(
            deps.fetcher, bucket_candidates, max_workers=deps.settings.max_workers
        )
        representative_duration = statistics.median(c.duration_s for c in bucket_candidates)
        profile, num_positional = build_cd_reference_profile(
            reference_logs, representative_duration
        )

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
        deps.store.write_profile(
            CohortProfile(
                cohort_id=cohort_id,
                n_members=num_positional,
                built_at=datetime.now(UTC),
                spells=profile,
            )
        )
        log.info(
            "cohort_builder.bucket_built",
            bucket_id=bucket_id,
            cohort_id=cohort_id,
            n_members=num_positional,
        )
        results.append(
            BucketBuildResult(
                bucket_id=bucket_id,
                duration_min_s=bucket_lo,
                duration_max_s=bucket_hi,
                n_members=num_positional,
                cohort_id=cohort_id,
            )
        )

    return results
