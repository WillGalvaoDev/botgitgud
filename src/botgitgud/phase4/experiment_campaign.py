"""SAE.2 — request and result types for the experimental Stage C campaign
(docs/fase4-statistical-architecture-experiment.md §6-§7).

Split from phase4/experiment_planner.py so both stay under the 300-line
limit (docs/implementacao.md T1.6's rule, enforced repo-wide). Pure data:
the planner owns the selection algorithm, these types only describe what was
asked for and what came out.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from typing import Literal, TypeVar

from botgitgud.domain.specs import SpecId
from botgitgud.phase4.experiment import (
    MIN_ENCOUNTERS_FOR_EXPERIMENT,
    MIN_SPECS_FOR_EXPERIMENT,
    ExperimentBudget,
)
from botgitgud.phase4.target import Phase4Target

StopReason = Literal["pool_exhausted", "max_observations", "budget_exhausted"]

_H = TypeVar("_H", bound=Hashable)


def count_by(values: Iterable[_H]) -> dict[_H, int]:
    counts: dict[_H, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


@dataclass(frozen=True, slots=True)
class StatisticalExperimentPlan:
    """The request. `partition` is a single int on purpose and
    `difficulties` must be stated explicitly — the planner refuses to guess
    either, since blending them is exactly the contamination §1.5 forbids.
    """

    partition: int
    difficulties: frozenset[int]
    budget: ExperimentBudget
    max_observations: int | None = None
    specs: frozenset[SpecId] | None = None  # None = every supported spec
    encounters: frozenset[int] | None = None  # None = every encounter found

    def __post_init__(self) -> None:
        if self.partition < 0:
            raise ValueError("partition must be non-negative")
        if not self.difficulties:
            raise ValueError("difficulties must be explicit and non-empty")
        if self.max_observations is not None and self.max_observations <= 0:
            raise ValueError("max_observations must be positive when given")
        if self.specs is not None and not self.specs:
            raise ValueError("specs must be None (all) or a non-empty set")
        if self.encounters is not None and not self.encounters:
            raise ValueError("encounters must be None (all) or a non-empty set")

    def observation_cap(self, pool_size: int) -> int:
        """The count ceiling, given how many candidates exist.

        The budget is deliberately NOT folded in here: it is enforced by the
        planner's incremental cost check, so `stopped_reason` can distinguish
        "the caller asked for this many" from "the money ran out". Folding it
        in also under-counted badly — observations sharing a fight cost 2
        points instead of 17, so a budget buys far more than
        `max_observations_solo()` suggests (measured on the real census:
        1.200 observations over 376 fights cost 8.040, not 20.400).
        `max_observations_solo()` stays the conservative number to *quote*
        before a campaign's shape is known.
        """
        if self.max_observations is None:
            return pool_size
        return min(self.max_observations, pool_size)


@dataclass(frozen=True, slots=True)
class PlannedExperimentObservation:
    report_code: str
    fight_id: int
    player_name: str
    class_name: str
    spec_name: str
    encounter_id: int
    difficulty: int
    partition: int
    rank_percent: float
    bucket: str
    start_time_ms: int

    @property
    def target(self) -> Phase4Target:
        return Phase4Target(
            SpecId(self.class_name, self.spec_name),
            self.encounter_id,
            self.difficulty,
            self.partition,
        )

    @property
    def spec_key(self) -> str:
        return f"{self.class_name}/{self.spec_name}"

    @property
    def fight_key(self) -> tuple[str, int]:
        return (self.report_code, self.fight_id)

    @property
    def observation_key(self) -> tuple[str, int, str]:
        """The natural key — the same one `logs` dedups on."""
        return (self.report_code, self.fight_id, self.player_name)


@dataclass(frozen=True, slots=True)
class ExperimentCampaign:
    request: StatisticalExperimentPlan
    observations: tuple[PlannedExperimentObservation, ...]
    candidates_available: int
    strata_total: int
    strata_covered: int
    estimated_api_points: float
    stopped_reason: StopReason

    @property
    def n_observations(self) -> int:
        return len(self.observations)

    @property
    def distinct_fights(self) -> int:
        return len({o.fight_key for o in self.observations})

    @property
    def distinct_reports(self) -> int:
        return len({o.report_code for o in self.observations})

    @property
    def by_spec(self) -> dict[str, int]:
        return count_by(o.spec_key for o in self.observations)

    @property
    def by_encounter(self) -> dict[int, int]:
        return count_by(o.encounter_id for o in self.observations)

    @property
    def by_bucket(self) -> dict[str, int]:
        return count_by(o.bucket for o in self.observations)

    @property
    def by_target(self) -> dict[str, int]:
        return count_by(o.target.target_id for o in self.observations)

    @property
    def temporal_span_ms(self) -> tuple[int, int] | None:
        if not self.observations:
            return None
        times = [o.start_time_ms for o in self.observations]
        return (min(times), max(times))

    @property
    def meets_minimum_coverage(self) -> bool:
        """Doc §14 stop criterion 2: below this, S3/S4 cannot be built and
        the experiment loses its object.
        """
        return (
            len(self.by_encounter) >= MIN_ENCOUNTERS_FOR_EXPERIMENT
            and len(self.by_spec) >= MIN_SPECS_FOR_EXPERIMENT
        )
