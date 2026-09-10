"""Pure assessment and dry-run planning for cohort cache invalidation.

This module deliberately contains no persistence or invalidation operation.  Its
output is inert data intended for human review before GATE-2 is authorized.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias, cast

from botgitgud.analysis.cohort import COHORT_MIN_HARD
from botgitgud.analysis.cohort_increment import CohortState

CohortPoolStatus: TypeAlias = Literal["valid", "incomplete", "semantically_invalid"]

DIFFICULTY_REASON = "declared_difficulty_is_not_mythic"
STATE_REASON = "cohort_state_is_not_ready"
MEMBER_FLOOR_REASON = "member_count_below_minimum"


@dataclass(frozen=True, slots=True)
class CohortPoolAssessment:
    cohort_id: str
    status: CohortPoolStatus
    reasons: tuple[str, ...]
    difficulty: int
    partition: int
    n_members: int

    def __post_init__(self) -> None:
        if bool(self.reasons) == (self.status == "valid"):
            raise ValueError("reasons must be empty exactly when status is valid")


@dataclass(frozen=True, slots=True)
class InvalidationPlan:
    to_invalidate: tuple[CohortPoolAssessment, ...]
    preserved: tuple[CohortPoolAssessment, ...]
    counts_by_difficulty_partition: tuple[tuple[int, int, int], ...]
    total: int

    @property
    def affected_total(self) -> int:
        return self.total

    @property
    def assessed_total(self) -> int:
        return len(self.to_invalidate) + len(self.preserved)


def assess_cohort_pool(row: Mapping[str, object]) -> CohortPoolAssessment:
    """Classify one registry-like row, with semantic invalidity taking precedence."""
    difficulty = int(cast(int, row["difficulty"]))
    n_members = int(cast(int, row.get("n_members", 0)))
    raw_state = row.get("state", CohortState.READY)
    state = raw_state.value if isinstance(raw_state, CohortState) else str(raw_state).lower()

    reasons: list[str] = []
    semantically_invalid = difficulty != 5
    incomplete = state != CohortState.READY.value or n_members < COHORT_MIN_HARD
    if semantically_invalid:
        reasons.append(DIFFICULTY_REASON)
    if state != CohortState.READY.value:
        reasons.append(STATE_REASON)
    if n_members < COHORT_MIN_HARD:
        reasons.append(MEMBER_FLOOR_REASON)

    status: CohortPoolStatus
    if semantically_invalid:
        status = "semantically_invalid"
    elif incomplete:
        status = "incomplete"
    else:
        status = "valid"
    return CohortPoolAssessment(
        cohort_id=str(row["cohort_id"]),
        status=status,
        reasons=tuple(reasons),
        difficulty=difficulty,
        partition=int(cast(int, row["partition"])),
        n_members=n_members,
    )


def plan_invalidation(rows: Iterable[Mapping[str, object]]) -> InvalidationPlan:
    """Return a deterministic plan; never mutate rows or any backing store."""
    assessments = sorted((assess_cohort_pool(row) for row in rows), key=_assessment_key)
    invalid = tuple(item for item in assessments if item.status == "semantically_invalid")
    preserved = tuple(item for item in assessments if item.status != "semantically_invalid")
    counts = Counter((item.difficulty, item.partition) for item in invalid)
    return InvalidationPlan(
        to_invalidate=invalid,
        preserved=preserved,
        counts_by_difficulty_partition=tuple(
            (difficulty, partition, count)
            for (difficulty, partition), count in sorted(counts.items())
        ),
        total=len(invalid),
    )


def _assessment_key(item: CohortPoolAssessment) -> tuple[int, int, str]:
    return (item.difficulty, item.partition, item.cohort_id)
