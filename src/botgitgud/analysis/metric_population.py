"""M2.2 — deterministic per-metric reference population selection.

See docs/m2-2-specification.md. For one ``metric_id`` (``"<metric>:<spell_id>"``),
selects the DESCRIPTIVE and ASPIRATIONAL reference populations from M2.1 basic
eligibility (analysis/reference_eligibility.py), the covariate matrix and
relaxation ladder of this SPEC, and M1's per-metric observation availability
(analysis/metric_observations.py). Does not wire into pipeline/cohort_match/
benchmark_reference (M2.3) and does not redefine any M1/M2.1 measurement or
eligibility semantics. Selection is a necessary, not sufficient, condition of
comparison; it is not adjustment and not proof of causality.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from botgitgud.analysis.benchmark_reference import REFERENCE_MIN_N, select_benchmark_reference
from botgitgud.analysis.cohort import COHORT_MIN_HARD, POSITIONAL_BAND_PCT, SANITY_BAND_PCT
from botgitgud.analysis.cohort_match import (
    DURATION_BANDS_PCT,
    DURATION_FLOOR_S,
    ITEM_LEVEL_BAND,
    TIER_PIECES_BAND,
)
from botgitgud.analysis.grading import MIN_N_FOR_GRADING
from botgitgud.analysis.measurement import MetricObservation, MetricStatus, damage_reference_id
from botgitgud.analysis.metric_observations import UNITS, observe
from botgitgud.analysis.reference_eligibility import (
    EligibilityDecision,
    ReferenceEligibility,
    ReferenceEligibilityPopulation,
)
from botgitgud.domain.external_buffs import EXTERNAL_OFFENSIVE_IDS
from botgitgud.domain.models import PlayerLog
from botgitgud.domain.spells import SpellCatalog

METRIC_POPULATION_POLICY_VERSION = "metric-population-v1"


class PopulationKind(StrEnum):
    DESCRIPTIVE = "DESCRIPTIVE"
    ASPIRATIONAL = "ASPIRATIONAL"


class Covariate(StrEnum):
    DURATION = "DURATION"
    EXTERNAL_BUFFS = "EXTERNAL_BUFFS"
    ITEM_LEVEL = "ITEM_LEVEL"
    TIER_PIECES = "TIER_PIECES"


class CovariateRole(StrEnum):
    REQUIRED = "REQUIRED"
    RELAXABLE = "RELAXABLE"
    NOT_ADMITTED = "NOT_ADMITTED"
    INADMISSIBLE_TARGET_UNKNOWN = "INADMISSIBLE_TARGET_UNKNOWN"


class SufficiencyState(StrEnum):
    INSUFFICIENT = "INSUFFICIENT"
    SUFFICIENT_FOR_COMPARISON = "SUFFICIENT_FOR_COMPARISON"
    SUFFICIENT_FOR_GRADING = "SUFFICIENT_FOR_GRADING"
    UNAVAILABLE = "UNAVAILABLE"


class RelaxationRule(StrEnum):
    DROP_FILTER = "DROP_FILTER"
    WIDEN_BAND = "WIDEN_BAND"


@dataclass(frozen=True, slots=True)
class RelaxationStep:
    covariate: Covariate
    rule: RelaxationRule
    band_index: int | None
    band_pct: float | None
    n_before: int
    n_after: int
    admitted_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MetricPopulation:
    policy_version: str
    eligibility_policy_version: str
    metric_id: str
    kind: PopulationKind
    target_id: str
    members: tuple[str, ...]
    n: int
    sufficiency: SufficiencyState
    matched_covariates: tuple[Covariate, ...]
    relaxed_covariates: tuple[Covariate, ...]
    declared_covariates: tuple[Covariate, ...]
    relaxation_steps: tuple[RelaxationStep, ...]
    final_duration_band_pct: float
    excluded_reasons: Mapping[str, tuple[str, ...]]
    declared_limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MetricPopulationSet:
    policy_version: str
    metric_id: str
    descriptive: MetricPopulation
    aspirational: MetricPopulation


@dataclass(frozen=True, slots=True)
class SensitivityRow:
    level_index: int
    relaxed_so_far: tuple[Covariate, ...]
    duration_band_pct: float
    n: int
    member_ids: tuple[str, ...]


# --- SPEC §6.2: ladders derived from existing constants, no new numbers ------

_DURATION_LADDER_WIDE: tuple[float, ...] = (*DURATION_BANDS_PCT, SANITY_BAND_PCT)
_DURATION_LADDER_POSITIONAL: tuple[float, ...] = tuple(
    pct for pct in DURATION_BANDS_PCT if pct <= POSITIONAL_BAND_PCT
)

# --- SPEC §5.3: matrix cells that differ from the REL default ---------------

_POSITIONAL_METRICS: frozenset[str] = frozenset({"player_casts_per_minute"})
_ILVL_TIER_NOT_ADMITTED_METRICS: frozenset[str] = frozenset(
    {"player_casts_per_minute", "aura_uptime_fraction"}
)

_COVARIATE_ORDER: tuple[Covariate, ...] = (
    Covariate.DURATION,
    Covariate.EXTERNAL_BUFFS,
    Covariate.ITEM_LEVEL,
    Covariate.TIER_PIECES,
)


def _duration_ladder(metric: str) -> tuple[float, ...]:
    return _DURATION_LADDER_POSITIONAL if metric in _POSITIONAL_METRICS else _DURATION_LADDER_WIDE


def _covariate_roles(metric: str, target: PlayerLog) -> dict[Covariate, CovariateRole]:
    """SPEC §5.3-5.4: item_level/tier_pieces are NOT_ADMITTED for metrics that
    are not amplitude measures; otherwise RELAXABLE, unless the target's own
    value is unknown, in which case the covariate is INADMISSIBLE (never a
    filter, never relaxed) rather than defaulted.
    """
    ilvl_tier_role = (
        CovariateRole.NOT_ADMITTED
        if metric in _ILVL_TIER_NOT_ADMITTED_METRICS
        else CovariateRole.RELAXABLE
    )
    roles: dict[Covariate, CovariateRole] = {
        Covariate.DURATION: CovariateRole.REQUIRED,
        Covariate.EXTERNAL_BUFFS: CovariateRole.REQUIRED,
        Covariate.ITEM_LEVEL: ilvl_tier_role,
        Covariate.TIER_PIECES: ilvl_tier_role,
    }
    if roles[Covariate.ITEM_LEVEL] is CovariateRole.RELAXABLE and target.build.item_level is None:
        roles[Covariate.ITEM_LEVEL] = CovariateRole.INADMISSIBLE_TARGET_UNKNOWN
    if roles[Covariate.TIER_PIECES] is CovariateRole.RELAXABLE and target.build.tier_pieces is None:
        roles[Covariate.TIER_PIECES] = CovariateRole.INADMISSIBLE_TARGET_UNKNOWN
    return roles


def _within_duration_band(candidate_s: float, target_s: float, pct: float) -> bool:
    tolerance = max(target_s * pct, DURATION_FLOOR_S)
    return abs(candidate_s - target_s) <= tolerance


def _covariate_check(
    candidate: PlayerLog,
    target: PlayerLog,
    active_relaxable: frozenset[Covariate],
    duration_pct: float,
) -> tuple[bool, tuple[str, ...]]:
    """SPEC §5.1/§5.4: duration and external_buffs are always checked
    (REQUIRED, never dropped); item_level/tier_pieces are checked only while
    still ``active_relaxable`` (RELAXABLE and not yet dropped by the ladder).
    Never defaults an unknown value on either side (C06).
    """
    reasons: list[str] = []
    if not _within_duration_band(candidate.fight.duration_s, target.fight.duration_s, duration_pct):
        reasons.append("DURATION_BAND_MISMATCH")

    candidate_buffs = candidate.build.external_buffs & EXTERNAL_OFFENSIVE_IDS
    target_buffs = target.build.external_buffs & EXTERNAL_OFFENSIVE_IDS
    if candidate_buffs != target_buffs:
        reasons.append("EXTERNAL_BUFFS_MISMATCH")

    if Covariate.ITEM_LEVEL in active_relaxable:
        target_ilvl = target.build.item_level
        assert target_ilvl is not None  # guaranteed by _covariate_roles gating
        if candidate.build.item_level is None:
            reasons.append("ITEM_LEVEL_UNKNOWN")
        elif abs(candidate.build.item_level - target_ilvl) > ITEM_LEVEL_BAND:
            reasons.append("ITEM_LEVEL_BAND_MISMATCH")

    if Covariate.TIER_PIECES in active_relaxable:
        target_tier = target.build.tier_pieces
        assert target_tier is not None  # guaranteed by _covariate_roles gating
        if candidate.build.tier_pieces is None:
            reasons.append("TIER_PIECES_UNKNOWN")
        elif abs(candidate.build.tier_pieces - target_tier) > TIER_PIECES_BAND:
            reasons.append("TIER_PIECES_BAND_MISMATCH")

    return (not reasons, tuple(reasons))


def _current_members(
    stage_a_admitted: Mapping[str, PlayerLog],
    metric_available: frozenset[str],
    target: PlayerLog,
    active_relaxable: frozenset[Covariate],
    duration_pct: float,
) -> frozenset[str]:
    """SPEC §8.1: membership requires ELIGIBLE (already filtered into
    ``stage_a_admitted``) AND metric AVAILABLE AND covariate admission —
    evaluated together at every ladder level, since the ladder counts N per
    §8.1's full definition, not covariate-passing alone.
    """
    return frozenset(
        rid
        for rid, log in stage_a_admitted.items()
        if rid in metric_available
        and _covariate_check(log, target, active_relaxable, duration_pct)[0]
    )


def _stage_a(
    references: Sequence[PlayerLog], eligibility: ReferenceEligibilityPopulation
) -> tuple[dict[str, PlayerLog], dict[str, ReferenceEligibility], dict[str, PlayerLog]]:
    """M2.1 §4 explicitly preserves repeated ``reference_id`` entries and does
    not establish a uniqueness precondition; M2.2 does not deduplicate either
    (that is ``cohort_match``'s job, M2.3). A same-id log and its eligibility
    decision are therefore grouped, never picked independently by first/last
    occurrence — picking independently is what let one physical log's
    covariate data pair with a *different* physical log's decision when the
    two disagreed (an M2.1-authority defect, not a product-dedup gap). When
    every log and every decision sharing an id agree, the collision is
    harmless and collapses deterministically; when they disagree, this
    refuses to guess which representation prevails and raises instead.
    """
    logs_by_id: dict[str, list[PlayerLog]] = {}
    for reference in references:
        logs_by_id.setdefault(damage_reference_id(reference), []).append(reference)

    results_by_id: dict[str, list[ReferenceEligibility]] = {}
    for result in eligibility.results:
        results_by_id.setdefault(result.reference_id, []).append(result)

    missing = sorted(rid for rid in logs_by_id if rid not in results_by_id)
    if missing:
        raise ValueError(f"references not present in eligibility population: {missing}")

    id_to_log: dict[str, PlayerLog] = {}
    elig_by_id: dict[str, ReferenceEligibility] = {}
    for rid in sorted(logs_by_id):
        logs = logs_by_id[rid]
        results = results_by_id[rid]
        if any(log != logs[0] for log in logs) or any(result != results[0] for result in results):
            raise ValueError(
                f"reference_id collision with disagreeing data for {rid!r}: M2.2 does not "
                "deduplicate references (M2.1 SPEC §4) and refuses to guess which log or "
                "eligibility decision represents this id"
            )
        id_to_log[rid] = logs[0]
        elig_by_id[rid] = results[0]

    stage_a_admitted = {
        rid: log
        for rid, log in id_to_log.items()
        if elig_by_id[rid].decision is EligibilityDecision.ELIGIBLE
    }
    return stage_a_admitted, elig_by_id, id_to_log


def _build_excluded_reasons(
    id_to_log: Mapping[str, PlayerLog],
    elig_by_id: Mapping[str, ReferenceEligibility],
    metric_available: frozenset[str],
    observations: Mapping[str, MetricObservation],
    current: frozenset[str],
    target: PlayerLog,
    active_relaxable: frozenset[Covariate],
    duration_pct: float,
) -> dict[str, tuple[str, ...]]:
    """SPEC §10.1: stage A (propagated M2.1 reasons, verbatim) takes
    precedence; then stage C (metric unavailable, propagated verbatim from
    M1); then stage B (covariate mismatch at the final ladder level).
    """
    excluded: dict[str, tuple[str, ...]] = {}
    for rid in sorted(id_to_log):  # SPEC §11: every output ordered by reference_id
        if rid in current:
            continue
        log = id_to_log[rid]
        result = elig_by_id[rid]
        if result.decision is EligibilityDecision.INELIGIBLE:
            excluded[rid] = ("BASIC_ELIGIBILITY_INELIGIBLE", *result.reasons)
        elif result.decision is EligibilityDecision.INDETERMINATE:
            excluded[rid] = ("BASIC_ELIGIBILITY_INDETERMINATE", *result.reasons)
        elif rid not in metric_available:
            excluded[rid] = observations[rid].reasons
        else:
            _, reasons = _covariate_check(log, target, active_relaxable, duration_pct)
            excluded[rid] = reasons
    return excluded


def _covariate_summary(
    roles: Mapping[Covariate, CovariateRole],
    active_relaxable: frozenset[Covariate],
    duration_idx: int,
) -> tuple[tuple[Covariate, ...], tuple[Covariate, ...], tuple[Covariate, ...]]:
    matched: set[Covariate] = {Covariate.DURATION, Covariate.EXTERNAL_BUFFS}
    relaxed: set[Covariate] = set()
    declared: set[Covariate] = set()
    for covariate in (Covariate.ITEM_LEVEL, Covariate.TIER_PIECES):
        role = roles[covariate]
        if role in (CovariateRole.NOT_ADMITTED, CovariateRole.INADMISSIBLE_TARGET_UNKNOWN):
            declared.add(covariate)
        elif covariate in active_relaxable:
            matched.add(covariate)
        else:
            relaxed.add(covariate)
    if duration_idx > 0:
        relaxed.add(Covariate.DURATION)
    matched_out = tuple(c for c in _COVARIATE_ORDER if c in matched)
    relaxed_out = tuple(c for c in _COVARIATE_ORDER if c in relaxed)
    declared_out = tuple(c for c in _COVARIATE_ORDER if c in declared)
    return matched_out, relaxed_out, declared_out


def _sorted_by_key(mapping: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    """SPEC §11: "Toda saída ordenada por reference_id" — makes that an
    explicit property of the output mapping itself (dict insertion order),
    not something that only holds after external normalization such as
    ``json.dumps(..., sort_keys=True)``.
    """
    return {rid: mapping[rid] for rid in sorted(mapping)}


def _sufficiency(n: int) -> SufficiencyState:
    if n < COHORT_MIN_HARD:
        return SufficiencyState.INSUFFICIENT
    if n < MIN_N_FOR_GRADING:
        return SufficiencyState.SUFFICIENT_FOR_COMPARISON
    return SufficiencyState.SUFFICIENT_FOR_GRADING


def _base_limitations(
    eligibility: ReferenceEligibilityPopulation, roles: Mapping[Covariate, CovariateRole]
) -> list[str]:
    limitations = list(eligibility.declared_limitations)
    if roles[Covariate.ITEM_LEVEL] is CovariateRole.INADMISSIBLE_TARGET_UNKNOWN:
        limitations.append("TARGET_ITEM_LEVEL_UNKNOWN")
    if roles[Covariate.TIER_PIECES] is CovariateRole.INADMISSIBLE_TARGET_UNKNOWN:
        limitations.append("TARGET_TIER_PIECES_UNKNOWN")
    return limitations


def _relaxation_limitations(steps: Sequence[RelaxationStep]) -> list[str]:
    return ["RELAXATION_APPLIED", "COVARIATE_ADJUSTMENT_UNVERIFIED"] if steps else []


def _sufficiency_limitations(n: int) -> list[str]:
    state = _sufficiency(n)
    if state is SufficiencyState.INSUFFICIENT:
        return ["INSUFFICIENT_FOR_COMPARISON"]
    if state is SufficiencyState.SUFFICIENT_FOR_COMPARISON:
        return ["INSUFFICIENT_FOR_GRADING"]
    return []


def _member_limitations(n: int, members_logs: Sequence[PlayerLog], target: PlayerLog) -> list[str]:
    if n == 0:
        return []
    result = ["SETUP_NOT_MATCHED"]
    if any(log.build.has_augmentation != target.build.has_augmentation for log in members_logs):
        result.append("AUGMENTATION_NOT_MATCHED")
    return result


def select_metric_population(
    target: PlayerLog,
    references: Sequence[PlayerLog],
    *,
    metric: str,
    spell_id: int,
    eligibility: ReferenceEligibilityPopulation,
    catalog: SpellCatalog | None,
) -> MetricPopulationSet:
    """Pure selection of the DESCRIPTIVE and ASPIRATIONAL population for one
    ``metric_id``. ``eligibility`` must already cover every id in
    ``references`` (M2.1 is not recomputed here) and its ``target_id`` must
    match ``target``; both violations raise ``ValueError`` rather than
    silently reconciling (SPEC §9).
    """
    if metric not in UNITS:
        raise ValueError(f"unknown metric {metric!r}; must be one of {sorted(UNITS)}")
    target_id = damage_reference_id(target)
    if eligibility.target_id != target_id:
        raise ValueError("eligibility.target_id does not match target")

    metric_id = f"{metric}:{spell_id}"
    stage_a_admitted, elig_by_id, id_to_log = _stage_a(references, eligibility)
    observations = {
        rid: observe(log, spell_id, metric, catalog) for rid, log in stage_a_admitted.items()
    }
    metric_available = frozenset(
        rid for rid, obs in observations.items() if obs.status is MetricStatus.AVAILABLE
    )

    roles = _covariate_roles(metric, target)
    duration_ladder = _duration_ladder(metric)
    active_relaxable = frozenset(
        c
        for c in (Covariate.ITEM_LEVEL, Covariate.TIER_PIECES)
        if roles[c] is CovariateRole.RELAXABLE
    )
    duration_idx = 0
    steps: list[RelaxationStep] = []

    current = _current_members(
        stage_a_admitted, metric_available, target, active_relaxable, duration_ladder[0]
    )
    if len(current) < COHORT_MIN_HARD:
        # SPEC §6.3: tier_pieces, then item_level, then duration widening.
        for covariate in (Covariate.TIER_PIECES, Covariate.ITEM_LEVEL):
            if len(current) >= COHORT_MIN_HARD:
                break
            if covariate not in active_relaxable:
                continue
            n_before = len(current)
            active_relaxable = active_relaxable - {covariate}
            new_current = _current_members(
                stage_a_admitted,
                metric_available,
                target,
                active_relaxable,
                duration_ladder[duration_idx],
            )
            admitted = tuple(sorted(new_current - current))
            steps.append(
                RelaxationStep(
                    covariate,
                    RelaxationRule.DROP_FILTER,
                    None,
                    None,
                    n_before,
                    len(new_current),
                    admitted,
                )
            )
            current = new_current
        while len(current) < COHORT_MIN_HARD and duration_idx < len(duration_ladder) - 1:
            n_before = len(current)
            duration_idx += 1
            new_current = _current_members(
                stage_a_admitted,
                metric_available,
                target,
                active_relaxable,
                duration_ladder[duration_idx],
            )
            admitted = tuple(sorted(new_current - current))
            steps.append(
                RelaxationStep(
                    Covariate.DURATION,
                    RelaxationRule.WIDEN_BAND,
                    duration_idx,
                    duration_ladder[duration_idx],
                    n_before,
                    len(new_current),
                    admitted,
                )
            )
            current = new_current

    final_duration_pct = duration_ladder[duration_idx]
    matched_covariates, relaxed_covariates, declared_covariates = _covariate_summary(
        roles, active_relaxable, duration_idx
    )
    excluded_reasons = _build_excluded_reasons(
        id_to_log,
        elig_by_id,
        metric_available,
        observations,
        current,
        target,
        active_relaxable,
        final_duration_pct,
    )
    base_limitations = _base_limitations(eligibility, roles)

    descriptive_members = tuple(sorted(current))
    descriptive_logs = [stage_a_admitted[rid] for rid in descriptive_members]
    descriptive_limitations = (
        *base_limitations,
        *_sufficiency_limitations(len(descriptive_members)),
        *_relaxation_limitations(steps),
        *_member_limitations(len(descriptive_members), descriptive_logs, target),
    )
    descriptive = MetricPopulation(
        policy_version=METRIC_POPULATION_POLICY_VERSION,
        eligibility_policy_version=eligibility.policy_version,
        metric_id=metric_id,
        kind=PopulationKind.DESCRIPTIVE,
        target_id=target_id,
        members=descriptive_members,
        n=len(descriptive_members),
        sufficiency=_sufficiency(len(descriptive_members)),
        matched_covariates=matched_covariates,
        relaxed_covariates=relaxed_covariates,
        declared_covariates=declared_covariates,
        relaxation_steps=tuple(steps),
        final_duration_band_pct=final_duration_pct,
        excluded_reasons=excluded_reasons,
        declared_limitations=descriptive_limitations,
    )

    aspirational = _select_aspirational(
        target=target,
        target_id=target_id,
        metric_id=metric_id,
        eligibility=eligibility,
        steps=steps,
        final_duration_pct=final_duration_pct,
        matched_covariates=matched_covariates,
        relaxed_covariates=relaxed_covariates,
        declared_covariates=declared_covariates,
        base_limitations=base_limitations,
        stage_a_admitted=stage_a_admitted,
        descriptive=descriptive,
    )

    return MetricPopulationSet(
        policy_version=METRIC_POPULATION_POLICY_VERSION,
        metric_id=metric_id,
        descriptive=descriptive,
        aspirational=aspirational,
    )


def _select_aspirational(
    *,
    target: PlayerLog,
    target_id: str,
    metric_id: str,
    eligibility: ReferenceEligibilityPopulation,
    steps: list[RelaxationStep],
    final_duration_pct: float,
    matched_covariates: tuple[Covariate, ...],
    relaxed_covariates: tuple[Covariate, ...],
    declared_covariates: tuple[Covariate, ...],
    base_limitations: list[str],
    stage_a_admitted: Mapping[str, PlayerLog],
    descriptive: MetricPopulation,
) -> MetricPopulation:
    """SPEC §7.2: subset of ``descriptive`` ordered by outcome (``log.dps``),
    never by the metric's own value. Members without a finite ``dps`` are
    excluded before the call — ``select_benchmark_reference`` treats
    ``dps is None`` as ``0.0`` and must never see such a member.
    """
    orderable_ids: list[str] = []
    unorderable_ids: list[str] = []
    for rid in descriptive.members:
        log = stage_a_admitted[rid]
        if log.dps is not None and math.isfinite(log.dps):
            orderable_ids.append(rid)
        else:
            unorderable_ids.append(rid)

    common = dict(descriptive.excluded_reasons)

    if len(orderable_ids) < REFERENCE_MIN_N:
        for rid in descriptive.members:
            common[rid] = ("ASPIRATIONAL_ORDER_UNAVAILABLE",)
        limitations = (
            *base_limitations,
            *_relaxation_limitations(steps),
            "ASPIRATIONAL_UNAVAILABLE",
        )
        return MetricPopulation(
            policy_version=METRIC_POPULATION_POLICY_VERSION,
            eligibility_policy_version=eligibility.policy_version,
            metric_id=metric_id,
            kind=PopulationKind.ASPIRATIONAL,
            target_id=target_id,
            members=(),
            n=0,
            sufficiency=SufficiencyState.UNAVAILABLE,
            matched_covariates=matched_covariates,
            relaxed_covariates=relaxed_covariates,
            declared_covariates=declared_covariates,
            relaxation_steps=tuple(steps),
            final_duration_band_pct=final_duration_pct,
            excluded_reasons=_sorted_by_key(common),  # SPEC §11: ordered by reference_id
            declared_limitations=limitations,
        )

    orderable_logs = [stage_a_admitted[rid] for rid in orderable_ids]
    selected = select_benchmark_reference(target, orderable_logs)
    selected_ids = frozenset(damage_reference_id(log) for log in selected)

    for rid in unorderable_ids:
        common[rid] = ("ASPIRATIONAL_ORDER_UNAVAILABLE",)
    for rid in orderable_ids:
        if rid not in selected_ids:
            common[rid] = ("ASPIRATIONAL_NOT_IN_UPPER_TAIL",)
    common = _sorted_by_key(common)  # SPEC §11: ordered by reference_id

    members = tuple(sorted(selected_ids))
    selected_logs = [stage_a_admitted[rid] for rid in members]
    limitations = (
        *base_limitations,
        *_sufficiency_limitations(len(members)),
        *_relaxation_limitations(steps),
        *_member_limitations(len(members), selected_logs, target),
        "ASPIRATIONAL_SELECTED_ON_OUTCOME",
        "ASPIRATIONAL_ORDERED_BY_WCL_DPS",
    )
    return MetricPopulation(
        policy_version=METRIC_POPULATION_POLICY_VERSION,
        eligibility_policy_version=eligibility.policy_version,
        metric_id=metric_id,
        kind=PopulationKind.ASPIRATIONAL,
        target_id=target_id,
        members=members,
        n=len(members),
        sufficiency=_sufficiency(len(members)),
        matched_covariates=matched_covariates,
        relaxed_covariates=relaxed_covariates,
        declared_covariates=declared_covariates,
        relaxation_steps=tuple(steps),
        final_duration_band_pct=final_duration_pct,
        excluded_reasons=common,
        declared_limitations=limitations,
    )


def select_metric_populations(
    target: PlayerLog,
    references: Sequence[PlayerLog],
    *,
    eligibility: ReferenceEligibilityPopulation,
    catalog: SpellCatalog | None,
    metric_ids: Sequence[tuple[str, int]] | None = None,
) -> Mapping[str, MetricPopulationSet]:
    """Maps ``select_metric_population`` over every ``(metric, spell_id)``
    pair. When ``metric_ids`` is omitted, enumerates every spell id observed
    in ``target`` or any ``references`` across all six contracted metrics —
    the same identity-union pattern ``metric_observations.compare_metrics``
    already uses to decide which spell ids to consider.
    """
    if metric_ids is None:
        spell_ids: set[int] = (
            set(target.damage_by_ability) | set(target.cast_timeline) | set(target.uptimes)
        )
        for reference in references:
            spell_ids.update(reference.damage_by_ability)
            spell_ids.update(reference.cast_timeline)
            spell_ids.update(reference.uptimes)
        pairs: list[tuple[str, int]] = [
            (metric, sid) for metric in sorted(UNITS) for sid in sorted(spell_ids)
        ]
    else:
        pairs = list(metric_ids)

    result: dict[str, MetricPopulationSet] = {}
    for metric, spell_id in pairs:
        population_set = select_metric_population(
            target,
            references,
            metric=metric,
            spell_id=spell_id,
            eligibility=eligibility,
            catalog=catalog,
        )
        result[population_set.metric_id] = population_set
    return result


def covariate_sensitivity(
    target: PlayerLog,
    references: Sequence[PlayerLog],
    *,
    metric: str,
    spell_id: int,
    eligibility: ReferenceEligibilityPopulation,
    catalog: SpellCatalog | None,
) -> tuple[SensitivityRow, ...]:
    """Read-only transparency artifact for B04 (SPEC §10.3). Walks every
    level of the relaxation ladder — without the floor early-stop that
    ``select_metric_population`` applies — so a reviewer can see N and
    membership at every level. Must never be consumed to choose a
    relaxation level, order or set; that choice belongs exclusively to
    ``select_metric_population``.
    """
    if metric not in UNITS:
        raise ValueError(f"unknown metric {metric!r}; must be one of {sorted(UNITS)}")
    target_id = damage_reference_id(target)
    if eligibility.target_id != target_id:
        raise ValueError("eligibility.target_id does not match target")

    stage_a_admitted, _, _ = _stage_a(references, eligibility)
    observations = {
        rid: observe(log, spell_id, metric, catalog) for rid, log in stage_a_admitted.items()
    }
    metric_available = frozenset(
        rid for rid, obs in observations.items() if obs.status is MetricStatus.AVAILABLE
    )

    roles = _covariate_roles(metric, target)
    duration_ladder = _duration_ladder(metric)
    active_relaxable = frozenset(
        c
        for c in (Covariate.ITEM_LEVEL, Covariate.TIER_PIECES)
        if roles[c] is CovariateRole.RELAXABLE
    )

    def _row(
        level_index: int, relaxed: tuple[Covariate, ...], active: frozenset[Covariate], d_idx: int
    ) -> SensitivityRow:
        current = _current_members(
            stage_a_admitted, metric_available, target, active, duration_ladder[d_idx]
        )
        return SensitivityRow(
            level_index, relaxed, duration_ladder[d_idx], len(current), tuple(sorted(current))
        )

    rows = [_row(0, (), active_relaxable, 0)]
    relaxed_so_far: tuple[Covariate, ...] = ()
    level = 1
    for covariate in (Covariate.TIER_PIECES, Covariate.ITEM_LEVEL):
        if covariate not in active_relaxable:
            continue
        active_relaxable = active_relaxable - {covariate}
        relaxed_so_far = (*relaxed_so_far, covariate)
        rows.append(_row(level, relaxed_so_far, active_relaxable, 0))
        level += 1
    for duration_idx in range(1, len(duration_ladder)):
        if Covariate.DURATION not in relaxed_so_far:
            relaxed_so_far = (*relaxed_so_far, Covariate.DURATION)
        rows.append(_row(level, relaxed_so_far, active_relaxable, duration_idx))
        level += 1
    return tuple(rows)
