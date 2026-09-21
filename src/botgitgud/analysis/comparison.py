"""T0.7 — replaces legacy/bot.py's compare_major_cds_clean.

Combines T0.5 (monotonic alignment) and T0.6 (cadence/classification) into
one comparison per spell, and fixes a second bug on top of achado 3.1:
legacy/bot.py:561 skips any spell the player never cast at all
(`if not user_times: continue`), hiding the worst possible finding — "you
never used this ability" — from the report entirely. Eligible spells with
zero player usage are now included; `align([], ref_times)` naturally
produces an all-MISSED alignment for them.

T2.3: each MATCH step also gets a StepGrade — quantile-relative grading
against that position's own raw reference distribution
(SpellProfile.slot_ref_times), replacing the old fixed 10s/25s thresholds
that used to live in report/text.py.

T2.4 (corrige achado 3.2, rotação-fantasma): compare_all_spells now aligns
PER PHASE INTERVAL (never across a phase/occurrence boundary) via
compare_spell_usage_by_phase, merging the per-interval sub-alignments back
into one chronologically-ordered Alignment. A fight with no declared
phases has exactly one interval spanning the whole fight (analysis/
phases.py's fallback), so this is a strict superset of the old flat
behavior — identical output for phase-less fights, never a regression.
compare_spell_usage (flat, phase-unaware) stays as-is for direct/simple
callers — it's still exercised by its own test suite and needs no change.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from botgitgud.analysis.alignment import Alignment, AlignmentKind, align
from botgitgud.analysis.cadence import SpellCadence, classify_cd_type, compute_cadence
from botgitgud.analysis.grading import (
    QuantileStats,
    bootstrap_median_ci,
    compute_quantile_stats,
    empirical_quantile,
    grade_deviation,
)
from botgitgud.domain.cooldowns import get_base_cooldown
from botgitgud.domain.models import PhaseInterval, PhaseKey, PlayerLog, SpellProfile
from botgitgud.domain.spells import SpellCatalog, SpellInfo


@dataclass(frozen=True, slots=True)
class StepGrade:
    grade: str  # analysis.grading.Grade
    quantile: float | None  # None only when grade == "insufficient"
    stats: QuantileStats
    ci90: tuple[float, float] | None


@dataclass(frozen=True, slots=True)
class SpellComparison:
    spell: SpellInfo
    cd_type: Literal["MAJOR", "MINOR"]
    cadence: SpellCadence
    presence: float
    alignment: Alignment
    reference_n: int
    # T2.3: index-aligned with alignment.steps — populated for MATCH steps
    # only (None for MISSED/EXTRA, which have no reference distribution to
    # grade against).
    step_grades: tuple[StepGrade | None, ...] = ()


def _grade_match_steps(
    alignment: Alignment, slot_ref_times: Sequence[Sequence[float]]
) -> tuple[StepGrade | None, ...]:
    grades: list[StepGrade | None] = []
    for step in alignment.steps:
        if step.kind is not AlignmentKind.MATCH:
            grades.append(None)
            continue
        assert step.ref_index is not None
        assert step.user_time is not None
        ref_dist = slot_ref_times[step.ref_index] if step.ref_index < len(slot_ref_times) else ()
        grade = grade_deviation(step.user_time, ref_dist)
        quantile = empirical_quantile(step.user_time, ref_dist) if ref_dist else None
        grades.append(
            StepGrade(
                grade=grade,
                quantile=quantile,
                stats=compute_quantile_stats(ref_dist),
                ci90=bootstrap_median_ci(ref_dist),
            )
        )
    return tuple(grades)


def compare_spell_usage(
    spell: SpellInfo,
    presence: float,
    user_times: Sequence[float],
    ref_times: Sequence[float],
    n_usages_median: float,
    reference_n: int,
    *,
    base_cooldown: float | None = None,
    gap_penalty: float = 25.0,  # mirrors Settings.gap_penalty_s's default; see compare_all_spells
    slot_ref_times: Sequence[Sequence[float]] = (),
) -> SpellComparison:
    """`slot_ref_times[i]` must be the raw distribution behind
    `ref_times[i]` — i.e. `ref_times` must already be sorted ascending
    before calling (true of SpellProfile.ref_times/slot_ref_times, the
    only production source), since align() re-sorts a copy internally and
    step.ref_index indexes into THAT order.
    """
    cadence = compute_cadence(
        ref_times, n_usages_median=n_usages_median, base_cooldown=base_cooldown
    )
    cd_type = classify_cd_type(cadence)
    alignment = align(sorted(user_times), sorted(ref_times), gap_penalty=gap_penalty)
    return SpellComparison(
        spell=spell,
        cd_type=cd_type,
        cadence=cadence,
        presence=presence,
        alignment=alignment,
        reference_n=reference_n,
        step_grades=_grade_match_steps(alignment, slot_ref_times),
    )


def compare_spell_usage_by_phase(
    spell: SpellInfo,
    presence: float,
    user_phase_times: Mapping[PhaseKey, Sequence[float]],
    ref_phase_times: Mapping[PhaseKey, Sequence[float]],
    intervals: Sequence[PhaseInterval],
    n_usages_median: float,
    reference_n: int,
    *,
    base_cooldown: float | None = None,
    gap_penalty: float = 25.0,  # mirrors Settings.gap_penalty_s's default; see compare_all_spells
    phase_slot_ref_times: Mapping[PhaseKey, Sequence[Sequence[float]]] | None = None,
    flat_ref_times: Sequence[float] = (),
) -> SpellComparison:
    """T2.4: aligns `user_phase_times` against `ref_phase_times`
    independently within each `(phase_id, occurrence)` key — a cast in one
    interval is NEVER paired against an expected usage from another (the
    "rotação-fantasma" achado 3.2 exists to eliminate), then merges the
    per-interval sub-alignments into one Alignment ordered by `intervals`'
    own chronological order. `flat_ref_times` (whole-fight, phase-agnostic)
    still drives cd_type classification — a spell's MAJOR/MINOR nature
    isn't a per-phase property.
    """
    phase_slot_ref_times = phase_slot_ref_times or {}
    order = {iv.key: idx for idx, iv in enumerate(intervals)}
    all_keys = sorted(
        set(user_phase_times) | set(ref_phase_times),
        key=lambda k: order.get(k, len(intervals)),
    )

    sub_alignments: list[Alignment] = []
    all_grades: list[StepGrade | None] = []
    total_cost = 0.0
    n_matched = n_missed = n_extra = 0
    for key in all_keys:
        u = sorted(user_phase_times.get(key, ()))
        r = sorted(ref_phase_times.get(key, ()))
        sub = align(u, r, gap_penalty=gap_penalty)
        sub_alignments.append(sub)
        total_cost += sub.total_cost
        n_matched += sub.n_matched
        n_missed += sub.n_missed
        n_extra += sub.n_extra
        all_grades.extend(_grade_match_steps(sub, phase_slot_ref_times.get(key, ())))

    merged = Alignment(
        steps=tuple(step for sub in sub_alignments for step in sub.steps),
        total_cost=total_cost,
        n_matched=n_matched,
        n_missed=n_missed,
        n_extra=n_extra,
    )
    cadence = compute_cadence(
        flat_ref_times, n_usages_median=n_usages_median, base_cooldown=base_cooldown
    )
    return SpellComparison(
        spell=spell,
        cd_type=classify_cd_type(cadence),
        cadence=cadence,
        presence=presence,
        alignment=merged,
        reference_n=reference_n,
        step_grades=tuple(all_grades),
    )


def compare_all_spells(
    player_log: PlayerLog,
    profile: Mapping[int, SpellProfile],
    eligible_spell_ids: Sequence[int],
    *,
    catalog: SpellCatalog,
    reference_n: int,
    gap_penalty: float = 25.0,  # see this function's own docstring below
) -> list[SpellComparison]:
    """T1.6/T2.4: replaces bot.py's compare_all_spells — one SpellComparison
    per eligible spell, in the given order (discover_eligible_spell_ids'
    own descending-presence order). Zero-usage abilities are never skipped
    (see this module's docstring). Alignment is phase-partitioned
    (compare_spell_usage_by_phase) — player_log.fight.phase_intervals is
    always populated (single fallback interval for phase-less fights), so
    this is the one production code path, not a special case.

    `gap_penalty`'s default mirrors Settings.gap_penalty_s's own default
    for direct/test callers that don't have a Settings object — the real
    pipeline (analysis/pipeline.py) always passes deps.settings.gap_penalty_s
    explicitly, so a `.env` override actually reaches the alignment cost.
    """
    comparisons: list[SpellComparison] = []
    for spell_id in eligible_spell_ids:
        if catalog.identity(spell_id).resolution_status == "unresolved":
            continue
        sp = profile[spell_id]
        comparisons.append(
            compare_spell_usage_by_phase(
                spell=catalog.get(spell_id),
                presence=sp.presence,
                user_phase_times=player_log.phase_cast_timeline.get(spell_id, {}),
                ref_phase_times=sp.phase_ref_times,
                intervals=player_log.fight.phase_intervals,
                n_usages_median=sp.n_usages_median,
                reference_n=(
                    sp.n_positional_with_spell
                    if sp.n_positional_with_spell is not None
                    else reference_n
                ),
                gap_penalty=gap_penalty,
                phase_slot_ref_times=sp.phase_slot_ref_times,
                flat_ref_times=sp.ref_times,
                base_cooldown=get_base_cooldown(spell_id),
            )
        )
    return comparisons
