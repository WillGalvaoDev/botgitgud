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
from botgitgud.domain.models import PlayerLog, SpellProfile
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
    gap_penalty: float = 25.0,
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


def compare_all_spells(
    player_log: PlayerLog,
    profile: Mapping[int, SpellProfile],
    eligible_spell_ids: Sequence[int],
    *,
    catalog: SpellCatalog,
    reference_n: int,
) -> list[SpellComparison]:
    """T1.6: replaces bot.py's compare_all_spells — one SpellComparison per
    eligible spell, in the given order (discover_eligible_spell_ids' own
    descending-presence order). Zero-usage abilities are never skipped
    (see this module's docstring).
    """
    comparisons: list[SpellComparison] = []
    for spell_id in eligible_spell_ids:
        sp = profile[spell_id]
        user_times = sorted(player_log.cast_timeline.get(spell_id, ()))
        comparisons.append(
            compare_spell_usage(
                spell=catalog.get(spell_id),
                presence=sp.presence,
                user_times=user_times,
                ref_times=sp.ref_times,
                n_usages_median=sp.n_usages_median,
                reference_n=reference_n,
                slot_ref_times=sp.slot_ref_times,
            )
        )
    return comparisons
