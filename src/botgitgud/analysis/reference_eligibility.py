"""M2.1 — deterministic basic eligibility policy for reference logs.

See docs/m2-1-specification.md. Decides IDENTITY, ATTEMPT_STATE, PARTITION,
DAMAGE_SCOPE and HOTFIX compatibility between exactly two logs — a necessary,
not sufficient, condition of comparison. Does not select covariates (M2.2),
does not wire into pipeline/cohort_match (M2.3) and does not redefine any
M1 measurement semantics.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from botgitgud.analysis.cohort import COHORT_MIN_HARD
from botgitgud.analysis.measurement import damage_reference_id
from botgitgud.domain.damage_scope import DamageScopeVersion
from botgitgud.domain.models import PlayerLog

REFERENCE_ELIGIBILITY_POLICY_VERSION = "reference-eligibility-v1"


class EligibilityDecision(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    INDETERMINATE = "INDETERMINATE"


class EligibilityAxis(StrEnum):
    IDENTITY = "IDENTITY"
    ATTEMPT_STATE = "ATTEMPT_STATE"
    PARTITION = "PARTITION"
    DAMAGE_SCOPE = "DAMAGE_SCOPE"
    HOTFIX = "HOTFIX"


@dataclass(frozen=True, slots=True)
class AxisVerdict:
    axis: EligibilityAxis
    decision: EligibilityDecision
    reasons: tuple[str, ...]
    observed_player: str | None
    observed_reference: str | None


@dataclass(frozen=True, slots=True)
class ReferenceEligibility:
    policy_version: str
    reference_id: str
    decision: EligibilityDecision
    reasons: tuple[str, ...]
    axes: tuple[AxisVerdict, ...]


@dataclass(frozen=True, slots=True)
class ReferenceEligibilityPopulation:
    policy_version: str
    target_id: str
    results: tuple[ReferenceEligibility, ...]
    eligible_ids: tuple[str, ...]
    indeterminate_ids: tuple[str, ...]
    ineligible_ids: tuple[str, ...]
    excluded_reasons: Mapping[str, tuple[str, ...]]
    declared_limitations: tuple[str, ...]
    n_eligible: int


_IDENTITY_INELIGIBLE_REASONS = frozenset(
    {
        "SELF_REFERENCE",
        "ENCOUNTER_MISMATCH",
        "DIFFICULTY_MISMATCH",
        "CLASS_MISMATCH",
        "SPEC_MISMATCH",
    }
)


def _is_blank(value: str) -> bool:
    return not value.strip()


def _normalized(value: str) -> str:
    return value.strip().casefold()


def _identity_snapshot(log: PlayerLog) -> str:
    return (
        f"class_name={log.build.class_name!r}, "
        f"difficulty={log.fight.difficulty!r}, "
        f"encounter_id={log.fight.encounter_id!r}, "
        f"spec_name={log.build.spec_name!r}"
    )


def _evaluate_identity(target: PlayerLog, reference: PlayerLog) -> AxisVerdict:
    reasons: list[str] = []
    if damage_reference_id(target) == damage_reference_id(reference):
        reasons.append("SELF_REFERENCE")
    if target.fight.encounter_id != reference.fight.encounter_id:
        reasons.append("ENCOUNTER_MISMATCH")
    if target.fight.difficulty != reference.fight.difficulty:
        reasons.append("DIFFICULTY_MISMATCH")

    player_class, ref_class = target.build.class_name, reference.build.class_name
    player_spec, ref_spec = target.build.spec_name, reference.build.spec_name
    class_unknown = _is_blank(player_class) or _is_blank(ref_class)
    spec_unknown = _is_blank(player_spec) or _is_blank(ref_spec)

    if not class_unknown and _normalized(player_class) != _normalized(ref_class):
        reasons.append("CLASS_MISMATCH")
    if not spec_unknown and _normalized(player_spec) != _normalized(ref_spec):
        reasons.append("SPEC_MISMATCH")
    if class_unknown or spec_unknown:
        reasons.append("IDENTITY_UNKNOWN")

    ordered = tuple(sorted(set(reasons)))
    if any(code in _IDENTITY_INELIGIBLE_REASONS for code in ordered):
        decision = EligibilityDecision.INELIGIBLE
    elif "IDENTITY_UNKNOWN" in ordered:
        decision = EligibilityDecision.INDETERMINATE
    else:
        decision = EligibilityDecision.ELIGIBLE

    return AxisVerdict(
        axis=EligibilityAxis.IDENTITY,
        decision=decision,
        reasons=ordered,
        observed_player=_identity_snapshot(target),
        observed_reference=_identity_snapshot(reference),
    )


def _attempt_state_snapshot(log: PlayerLog) -> str:
    return f"duration_s={log.fight.duration_s!r}, kill={log.fight.kill!r}"


def _valid_duration(duration_s: float) -> bool:
    return math.isfinite(duration_s) and duration_s > 0


def _evaluate_attempt_state(target: PlayerLog, reference: PlayerLog) -> AxisVerdict:
    reasons: list[str] = []
    if not reference.fight.kill:
        reasons.append("ATTEMPT_STATE_NOT_KILL")
    if not _valid_duration(target.fight.duration_s) or not _valid_duration(
        reference.fight.duration_s
    ):
        reasons.append("INVALID_DURATION")
    ordered = tuple(sorted(set(reasons)))
    decision = EligibilityDecision.INELIGIBLE if ordered else EligibilityDecision.ELIGIBLE
    return AxisVerdict(
        axis=EligibilityAxis.ATTEMPT_STATE,
        decision=decision,
        reasons=ordered,
        observed_player=_attempt_state_snapshot(target),
        observed_reference=_attempt_state_snapshot(reference),
    )


def _evaluate_partition(target: PlayerLog, reference: PlayerLog) -> AxisVerdict:
    player_partition = target.fight.partition
    ref_partition = reference.fight.partition
    if player_partition is None or ref_partition is None:
        decision, reasons = EligibilityDecision.INDETERMINATE, ("PARTITION_UNKNOWN",)
    elif player_partition != ref_partition:
        decision, reasons = EligibilityDecision.INELIGIBLE, ("PARTITION_MISMATCH",)
    else:
        decision, reasons = EligibilityDecision.ELIGIBLE, ()
    return AxisVerdict(
        axis=EligibilityAxis.PARTITION,
        decision=decision,
        reasons=reasons,
        observed_player=None if player_partition is None else str(player_partition),
        observed_reference=None if ref_partition is None else str(ref_partition),
    )


def _evaluate_damage_scope(target: PlayerLog, reference: PlayerLog) -> AxisVerdict:
    player_scope = target.damage_scope
    ref_scope = reference.damage_scope
    if (
        player_scope is DamageScopeVersion.UNRECONCILED
        or ref_scope is DamageScopeVersion.UNRECONCILED
    ):
        decision, reasons = EligibilityDecision.INELIGIBLE, ("SCOPE_UNRECONCILED",)
    elif player_scope != ref_scope:
        decision, reasons = EligibilityDecision.INELIGIBLE, ("SCOPE_MISMATCH",)
    else:
        decision, reasons = EligibilityDecision.ELIGIBLE, ()
    return AxisVerdict(
        axis=EligibilityAxis.DAMAGE_SCOPE,
        decision=decision,
        reasons=reasons,
        observed_player=player_scope.value,
        observed_reference=ref_scope.value,
    )


def _evaluate_hotfix(target: PlayerLog, reference: PlayerLog) -> AxisVerdict:
    """No persisted metadata carries build/patch/hotfix version (SPEC §5.5):
    this axis always abstains rather than fabricating temporal equivalence.
    """
    del target, reference
    return AxisVerdict(
        axis=EligibilityAxis.HOTFIX,
        decision=EligibilityDecision.ELIGIBLE,
        reasons=("HOTFIX_NOT_OBSERVABLE",),
        observed_player=None,
        observed_reference=None,
    )


_AXIS_EVALUATORS = (
    _evaluate_identity,
    _evaluate_attempt_state,
    _evaluate_partition,
    _evaluate_damage_scope,
    _evaluate_hotfix,
)


def evaluate_reference(target: PlayerLog, reference: PlayerLog) -> ReferenceEligibility:
    """Pure function of exactly ``target`` and ``reference``.

    Never depends on any other reference, the size of any set, cohort
    floors, global configuration, the clock or randomness (SPEC §4).
    """
    axes = tuple(evaluator(target, reference) for evaluator in _AXIS_EVALUATORS)
    decisions = {axis.decision for axis in axes}
    if EligibilityDecision.INELIGIBLE in decisions:
        decision = EligibilityDecision.INELIGIBLE
    elif EligibilityDecision.INDETERMINATE in decisions:
        decision = EligibilityDecision.INDETERMINATE
    else:
        decision = EligibilityDecision.ELIGIBLE
    reasons = tuple(reason for axis in axes for reason in axis.reasons)
    return ReferenceEligibility(
        policy_version=REFERENCE_ELIGIBILITY_POLICY_VERSION,
        reference_id=damage_reference_id(reference),
        decision=decision,
        reasons=reasons,
        axes=axes,
    )


def evaluate_references(
    target: PlayerLog, references: Sequence[PlayerLog]
) -> ReferenceEligibilityPopulation:
    """Map ``evaluate_reference`` over ``references`` and aggregate.

    One result per input item; a repeated ``reference_id`` is neither
    merged nor deduplicated here (SPEC §4) — that responsibility belongs
    to ``cohort_match`` (M2.3).
    """
    results = tuple(evaluate_reference(target, reference) for reference in references)
    eligible_ids = tuple(
        r.reference_id for r in results if r.decision is EligibilityDecision.ELIGIBLE
    )
    indeterminate_ids = tuple(
        r.reference_id for r in results if r.decision is EligibilityDecision.INDETERMINATE
    )
    ineligible_ids = tuple(
        r.reference_id for r in results if r.decision is EligibilityDecision.INELIGIBLE
    )
    excluded_reasons = {
        r.reference_id: r.reasons for r in results if r.decision is not EligibilityDecision.ELIGIBLE
    }
    n_eligible = len(eligible_ids)

    limitations: list[str] = []
    if not target.fight.kill:
        limitations.append("TARGET_ATTEMPT_NOT_KILL")
    if n_eligible > 0:
        limitations.append("HOTFIX_COMPATIBILITY_UNVERIFIED")
    if n_eligible < COHORT_MIN_HARD:
        limitations.append("INSUFFICIENT_ELIGIBLE_REFERENCES")

    return ReferenceEligibilityPopulation(
        policy_version=REFERENCE_ELIGIBILITY_POLICY_VERSION,
        target_id=damage_reference_id(target),
        results=results,
        eligible_ids=eligible_ids,
        indeterminate_ids=indeterminate_ids,
        ineligible_ids=ineligible_ids,
        excluded_reasons=excluded_reasons,
        declared_limitations=tuple(limitations),
        n_eligible=n_eligible,
    )
