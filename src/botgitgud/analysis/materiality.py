"""M29: materiality (RB-1..RB-3) and the Conclusion/positive-observation
objects (RB-4/RB-5) — additive layer, same spirit as M28's remediation.py.

M29 does not rank across kinds, does not cut to 0..3, and does not write a
sentence — that is M31/M30. This module only answers two questions:

1. Which candidates (ABILITY_GAP `Finding`, UPTIME `RelevanceFinding`,
   `ExecutionFinding`) may occupy a coaching slot at all (RB-1..RB-3)?
2. Where does the player stand, measured against the cohort the analysis
   actually used (RB-4), and is there at most one measured, favourable
   fact worth surfacing (RB-5)?

RB-1: one rule, three kinds, the SAME `grade_scalar`/`_grade_one_tailed`
M27 already established — never a new cutoff, never a new severity scale
(INVARIANT 1). `insufficient` is never material (INVARIANT 2): no
comparison base, no claim. A deviation in the player's favour is never
material (INVARIANT 3) — for UPTIME this falls out for free, because
`grade_scalar` is already direction-aware/one-tailed (see
performance_features.py's own module docstring): a favourable deviation
grades green, never red/yellow, so checking `grade in {red, yellow}` is
the whole rule, not half of it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from botgitgud.analysis.dps_gap import AbilityGap, DpsGapReport
from botgitgud.analysis.findings import ExecutionFinding, Finding, RelevanceFinding
from botgitgud.analysis.grading import Grade
from botgitgud.analysis.performance_features import (
    PerformanceFindings,
    ScalarFinding,
    grade_scalar,
)
from botgitgud.analysis.remediation import FindingRemediation, Remediation

# RB-1/INVARIANT 1: the only materiality test, everywhere — no new
# constant, no new scale. `insufficient` and `green` are both excluded by
# construction (INVARIANT 2/3).
_MATERIAL_GRADES = frozenset({"red", "yellow"})


class PositiveObservationBasis(StrEnum):
    """RB-5: enumerated basis for the (at most one) positive observation —
    never a free-text field (INVARIANT 6)."""

    ABILITY_ABOVE_COHORT = "ABILITY_ABOVE_COHORT"
    NO_DEATH = "NO_DEATH"
    ACTIVE_TIME = "ACTIVE_TIME"
    OVERALL_STANDING = "OVERALL_STANDING"


@dataclass(frozen=True, slots=True)
class PositiveObservation:
    """A base + a subject, nothing else — same discipline as M28's
    `Remediation`. `subject` is a spell_id only for
    `ABILITY_ABOVE_COHORT`; every other basis carries `None`."""

    basis: PositiveObservationBasis
    subject: int | None = None


@dataclass(frozen=True, slots=True)
class Sample:
    """RB-4: qualification of the comparison, never impact. `matched_n` is
    the paired cohort size; `relaxed_covariates` names which covariates
    were relaxed to reach it — both carried verbatim, no derived score."""

    matched_n: int
    relaxed_covariates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Conclusion:
    """RB-4: where the player stands, as an object, never a sentence
    (INVARIANT 6). `standing` and `percentile` are two DIFFERENT facts
    about two DIFFERENT populations — the paired cohort `analyze_dps_gap`
    actually compared against, and the WCL global ranking — and MUST stay
    separate fields; collapsing them into one number would be exactly the
    proxy-presented-as-fact this project's contract (§11) forbids."""

    standing: ScalarFinding
    percentile: float | None
    sample: Sample
    material_count: int


@dataclass(frozen=True, slots=True)
class MaterialCandidate:
    """An M28-eligible candidate carrying M29's discrete material band.

    The grade is copied verbatim from the candidate's authoritative scalar.
    In particular, this object contains no cross-kind score and does not turn
    the scalar's raw quantile into a comparable magnitude.
    """

    finding: Finding | RelevanceFinding | ExecutionFinding
    remediation: Remediation
    grade: Grade
    # Set by M31 when the final lexical key, rather than evidence, is needed
    # to place this candidate relative to another one. M30 can therefore
    # avoid presenting that placement as an evidence-backed rank.
    ordering_is_arbitrary: bool = False


def _ability_finding_pairs(
    dps_gap: DpsGapReport, findings: Sequence[Finding]
) -> list[tuple[Finding, AbilityGap]]:
    """The exact positional pairing `remediation.py`'s `build_remediations`
    already established: `build_findings` emits ABILITY_GAP `Finding`s, in
    order, from `dps_gap.abilities`' negative-delta subset — paired by
    that shared, authoritative order, never reconstructed from
    title/detail text.
    """
    abilities = (
        [item for item in dps_gap.abilities if item.delta_dps_pct < 0]
        if dps_gap.quantitative_damage_available
        else []
    )
    return list(zip(findings, abilities, strict=True))


def is_material_ability(ability: AbilityGap) -> bool:
    """RB-2: material iff the ability's cohort-relative share grade (the
    field `dps_gap.py` now attaches) is red/yellow. `None` (no cohort to
    grade) and `insufficient` (cohort too small) are both non-material."""
    return ability.cohort_share is not None and ability.cohort_share.grade in _MATERIAL_GRADES


def is_material_uptime(scalar: ScalarFinding | None) -> bool:
    """RB-3: `scalar` is the SAME `ScalarFinding` `performance_features.py`
    already computed (direction="higher_better") for this uptime — its
    grade is already one-tailed, so a quantile above the cohort (a
    favourable deviation) already grades green, never red/yellow. No
    second check is needed to satisfy RB-3; using the real grade IS the
    fix, in place of `RelevanceFinding`'s own two-sided `severity`."""
    return scalar is not None and scalar.grade in _MATERIAL_GRADES


def is_material_execution(finding: ExecutionFinding) -> bool:
    """RB-1: `ExecutionFinding` already satisfies this by construction
    (M27/RB-2 only ever appends red/yellow) — checked here anyway so this
    module never silently trusts an upstream invariant it cannot see."""
    return finding.finding.grade in _MATERIAL_GRADES


def _uptime_scalars_by_spell(
    performance: PerformanceFindings | None,
) -> dict[int, ScalarFinding]:
    if performance is None:
        return {}
    return {uptime.spell.spell_id: uptime.finding for uptime in performance.uptimes}


def collect_material_candidates(
    *,
    dps_gap: DpsGapReport,
    remediations: Sequence[FindingRemediation],
    performance: PerformanceFindings | None,
) -> tuple[MaterialCandidate, ...]:
    """Expose the material set after the existing M28 and M29 gates.

    `build_remediations` preserves the authoritative ABILITY_GAP order used
    by `build_findings`, so ability findings are paired positionally with the
    negative gaps exactly as in M28/M29.  Eligibility is consumed verbatim;
    it is never reconstructed here.
    """
    abilities = (
        [item for item in dps_gap.abilities if item.delta_dps_pct < 0]
        if dps_gap.quantitative_damage_available
        else []
    )
    ability_index = 0
    uptime_scalars = _uptime_scalars_by_spell(performance)
    candidates: list[MaterialCandidate] = []

    for item in remediations:
        finding = item.finding
        scalar: ScalarFinding | None = None

        if isinstance(finding, Finding) and finding.kind == "ABILITY_GAP":
            if ability_index >= len(abilities):
                raise ValueError("ABILITY_GAP remediation has no matching ability")
            ability = abilities[ability_index]
            ability_index += 1
            scalar = ability.cohort_share
        elif isinstance(finding, RelevanceFinding) and finding.kind == "UPTIME":
            spell_id = finding.evidence.get("spell_id")
            if isinstance(spell_id, int):
                scalar = uptime_scalars.get(spell_id)
        elif isinstance(finding, ExecutionFinding):
            scalar = finding.finding

        if item.coaching_eligible and scalar is not None and scalar.grade in _MATERIAL_GRADES:
            candidates.append(MaterialCandidate(finding, item.remediation, scalar.grade))

    if ability_index != len(abilities):
        raise ValueError("ability findings and dps-gap abilities differ in length")
    return tuple(candidates)


def count_material(
    *,
    dps_gap: DpsGapReport,
    findings: Sequence[Finding],
    relevance_findings: Sequence[RelevanceFinding],
    execution_findings: Sequence[ExecutionFinding],
    performance: PerformanceFindings | None,
) -> int:
    """RB-4's `material_count`: how many candidates, across the three
    kinds, survive RB-1..RB-3. The empty set (0) is a valid, expected
    result (INVARIANT 4) — never special-cased."""
    ability_findings = [f for f in findings if f.kind == "ABILITY_GAP"]
    pairs = _ability_finding_pairs(dps_gap, ability_findings)
    material = sum(1 for _, ability in pairs if is_material_ability(ability))

    uptime_scalars = _uptime_scalars_by_spell(performance)
    for f in relevance_findings:
        if f.kind != "UPTIME":
            continue
        spell_id = f.evidence.get("spell_id")
        scalar = uptime_scalars.get(spell_id) if isinstance(spell_id, int) else None
        if is_material_uptime(scalar):
            material += 1

    material += sum(1 for f in execution_findings if is_material_execution(f))
    return material


def build_conclusion(
    *,
    player_dps: float,
    cohort_dps_values: Sequence[float],
    percentile: float | None,
    matched_n: int,
    relaxed_covariates: Sequence[str],
    material_count: int,
) -> Conclusion:
    """RB-4: `standing` is `grade_scalar` of the player's DPS against the
    PAIRED cohort's DPS values — the same population `analyze_dps_gap`
    actually compared, never the global WCL ranking `percentile` carries
    (already available on the report header; passed through verbatim,
    never recomputed here)."""
    standing = grade_scalar(player_dps, cohort_dps_values, "higher_better")
    sample = Sample(matched_n=matched_n, relaxed_covariates=tuple(relaxed_covariates))
    return Conclusion(
        standing=standing,
        percentile=percentile,
        sample=sample,
        material_count=material_count,
    )


def _favorability(scalar: ScalarFinding) -> float | None:
    """How far into the "good" tail `scalar` sits, direction-normalised —
    a sort key for RB-5's "strongest favourable evidence", never a
    materiality decision (that stays `scalar.grade`, computed elsewhere).
    """
    if scalar.quantile is None:
        return None
    return scalar.quantile if scalar.direction == "higher_better" else 1.0 - scalar.quantile


def select_positive_observation(
    *,
    dps_gap: DpsGapReport,
    performance: PerformanceFindings | None,
    standing: ScalarFinding,
) -> PositiveObservation | None:
    """RB-5: at most one, chosen deterministically by favourability
    (quantile-based), with a stable tie-break — independent of the order
    candidates arrive in (TEST_PLAN item 8). Zero candidates is valid and
    common (INVARIANT 4's sibling for the positive side): a player with
    nothing measured and favourable gets no invented praise.

    Candidates, all literally "already measured and favourable" (RB-5),
    never inferred:
      - ABILITY_ABOVE_COHORT: a gated ability whose cohort_share grades
        green AND sits strictly above the cohort (quantile > 0.5) — RB-5
        names this one specifically as "above the cohort", a stricter bar
        than merely not-red/yellow.
      - ACTIVE_TIME: the matching `performance_features.py` scalar graded
        green (its own direction-aware grade already).
      - NO_DEATH: the deaths scalar graded green AND the measured death
        count is actually zero — grade alone is a cohort-relative proxy,
        not the absolute fact this basis asserts (§11/§18.4).
      - OVERALL_STANDING: the Conclusion's own `standing` graded green.
    """
    candidates: list[tuple[float, PositiveObservationBasis, int]] = []

    for ability in dps_gap.abilities:
        scalar = ability.cohort_share
        if scalar is None or scalar.grade != "green":
            continue
        favorability = _favorability(scalar)
        if favorability is None or favorability <= 0.5:
            continue
        candidates.append(
            (favorability, PositiveObservationBasis.ABILITY_ABOVE_COHORT, ability.spell.spell_id)
        )

    if performance is not None:
        # NO_DEATH asserts an absolute fact ("the player did not die"),
        # unlike every other basis here which only claims a cohort-relative
        # standing. `grade == "green"` alone means "not materially worse
        # than the cohort" — it does NOT mean zero deaths (a player who
        # died once can still grade green against a cohort where dying is
        # common). Requiring `user_value == 0.0` ties the claim to the
        # actual measurement instead of the proxy grade (§11/§18.4).
        if performance.deaths.grade == "green" and performance.deaths.user_value == 0.0:
            favorability = _favorability(performance.deaths)
            if favorability is not None:
                candidates.append((favorability, PositiveObservationBasis.NO_DEATH, 0))
        active_time_candidate = (
            performance.active_time if performance.active_time is not None else performance.downtime
        )
        if active_time_candidate.grade == "green":
            favorability = _favorability(active_time_candidate)
            if favorability is not None:
                candidates.append((favorability, PositiveObservationBasis.ACTIVE_TIME, 0))

    if standing.grade == "green":
        favorability = _favorability(standing)
        if favorability is not None:
            candidates.append((favorability, PositiveObservationBasis.OVERALL_STANDING, 0))

    if not candidates:
        return None

    # Highest favourability first; stable tie-break by basis name, then by
    # spell_id — deterministic regardless of the caller's input order,
    # since it depends only on the (favorability, basis, subject) values
    # themselves, never on list position.
    candidates.sort(key=lambda item: (-item[0], item[1].value, item[2]))
    favorability, basis, subject = candidates[0]
    is_ability = basis is PositiveObservationBasis.ABILITY_ABOVE_COHORT
    return PositiveObservation(basis=basis, subject=subject if is_ability else None)
