"""Phase4Target density and coverage from the frozen plan — how many
observations each target already has (not assumed uniform), and how much
completing the remaining pending rows could plausibly change that.
Read-only over `StoredCampaign`, no model fitting (docs/fase4-architecture-
decision.md §10-11). Split out of `experiment_decision.py` to keep that
module under the line-count convention — this is a distinct concern
(frozen-plan bookkeeping, not model evaluation).
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from botgitgud.phase4.experiment_store import CollectionStatus, StoredCampaign


def _percentile(sorted_values: Sequence[int], pct: float) -> float:
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    import math

    k = (len(sorted_values) - 1) * pct / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return float(sorted_values[int(k)])
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


@dataclass(frozen=True, slots=True)
class DensityReport:
    n_groups: int
    min_obs: int
    median_obs: float
    mean_obs: float
    p75_obs: float
    p90_obs: float
    max_obs: int
    n_groups_at_least: dict[int, int]


def _density_from_counts(counts: dict[str, int], thresholds: Sequence[int]) -> DensityReport:
    if not counts:
        return DensityReport(0, 0, 0.0, 0.0, 0.0, 0.0, 0, dict.fromkeys(thresholds, 0))
    values = sorted(counts.values())
    return DensityReport(
        n_groups=len(values),
        min_obs=values[0],
        median_obs=statistics.median(values),
        mean_obs=statistics.mean(values),
        p75_obs=_percentile(values, 75),
        p90_obs=_percentile(values, 90),
        max_obs=values[-1],
        n_groups_at_least={t: sum(1 for v in values if v >= t) for t in thresholds},
    )


def phase4_target_density(
    stored: StoredCampaign,
    *,
    only_completed: bool = False,
    thresholds: Sequence[int] = (10, 20, 30, 50),
) -> DensityReport:
    """Counts *planned* rows per Phase4Target from the frozen plan — every
    planned row is a slot that collection can eventually fill, so this
    answers "if every pending row completes, how many targets would reach
    threshold X" without assuming a uniform distribution across targets.
    """
    counts: dict[str, int] = {}
    for item in stored.observations:
        if only_completed and item.status is not CollectionStatus.COMPLETED:
            continue
        key = item.planned.target.target_id
        counts[key] = counts.get(key, 0) + 1
    return _density_from_counts(counts, thresholds)


@dataclass(frozen=True, slots=True)
class CoverageComparison:
    current_specs: int
    current_encounters: int
    current_targets: int
    planned_specs: int
    planned_encounters: int
    planned_targets: int


def coverage_completed_vs_planned(stored: StoredCampaign) -> CoverageComparison:
    current_specs: set[str] = set()
    current_encounters: set[int] = set()
    current_targets: set[str] = set()
    planned_specs: set[str] = set()
    planned_encounters: set[int] = set()
    planned_targets: set[str] = set()
    for item in stored.observations:
        planned_specs.add(item.planned.spec_key)
        planned_encounters.add(item.planned.encounter_id)
        planned_targets.add(item.planned.target.target_id)
        if item.status is CollectionStatus.COMPLETED:
            current_specs.add(item.planned.spec_key)
            current_encounters.add(item.planned.encounter_id)
            current_targets.add(item.planned.target.target_id)
    return CoverageComparison(
        current_specs=len(current_specs),
        current_encounters=len(current_encounters),
        current_targets=len(current_targets),
        planned_specs=len(planned_specs),
        planned_encounters=len(planned_encounters),
        planned_targets=len(planned_targets),
    )


class ArchitectureDecision(StrEnum):
    """§12 vocabulary — assigned by reasoned reading of the measured
    evidence (docs/fase4-architecture-decision.md §17), never a mechanical
    score: the task brief explicitly forbids an arbitrary scoring formula.
    """

    REJECT_FOR_CURRENT_PHASE4 = "reject_for_current_phase4"
    KEEP_AS_SECONDARY_CANDIDATE = "keep_as_secondary_candidate"
    ADVANCE_TO_VALIDATION = "advance_to_validation"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NOT_EVALUATED = "not_evaluated"
