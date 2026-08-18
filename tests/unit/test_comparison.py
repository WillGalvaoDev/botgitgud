from __future__ import annotations

from botgitgud.analysis.alignment import AlignmentKind
from botgitgud.analysis.comparison import compare_spell_usage, compare_spell_usage_by_phase
from botgitgud.analysis.phases import derive_phase_intervals
from botgitgud.domain.spells import SpellInfo


def _spell(spell_id: int = 1, name: str = "Test Spell") -> SpellInfo:
    return SpellInfo(spell_id=spell_id, name=name, source="wcl")


def test_zero_user_usage_produces_all_missed_alignment() -> None:
    """achado 3.1 (o segundo bug): legacy pulava spells com user_times vazio.
    A função de comparação em si já suporta isso — o all-MISSED alignment é
    o dado que a T0.7 usa para incluir a habilidade no relatório mesmo assim.
    """
    comparison = compare_spell_usage(
        spell=_spell(),
        presence=0.9,
        user_times=[],
        ref_times=[10.0, 130.0, 250.0, 370.0],
        n_usages_median=4.0,
        reference_n=10,
    )
    assert comparison.alignment.n_missed == 4
    assert comparison.alignment.n_matched == 0
    assert all(s.kind is AlignmentKind.MISSED for s in comparison.alignment.steps)


def test_classification_flows_through_from_cadence() -> None:
    comparison = compare_spell_usage(
        spell=_spell(),
        presence=0.9,
        user_times=[100.0],
        ref_times=[100.0],  # single ref usage, base_cooldown unknown -> MAJOR per T0.6
        n_usages_median=1.0,
        reference_n=5,
    )
    assert comparison.cd_type == "MAJOR"


def test_reference_n_and_presence_are_preserved() -> None:
    comparison = compare_spell_usage(
        spell=_spell(spell_id=42, name="Fireball"),
        presence=0.75,
        user_times=[10.0],
        ref_times=[10.0, 20.0],
        n_usages_median=2.0,
        reference_n=17,
    )
    assert comparison.reference_n == 17
    assert comparison.presence == 0.75
    assert comparison.spell.spell_id == 42


def test_extra_usage_is_represented_without_masking() -> None:
    comparison = compare_spell_usage(
        spell=_spell(),
        presence=0.8,
        user_times=[10.0, 40.0, 70.0],
        ref_times=[10.0, 40.0],
        n_usages_median=2.0,
        reference_n=8,
    )
    assert comparison.alignment.n_extra == 1
    extra_steps = [s for s in comparison.alignment.steps if s.kind is AlignmentKind.EXTRA]
    assert extra_steps[0].delta is None  # never a masked 0.0


# -- T2.3: per-step quantile grading -------------------------------------------------


def test_match_steps_get_a_step_grade_none_for_missed_and_extra() -> None:
    ref_dist = [float(i) for i in range(1, 21)]  # 20 pts, median ~10.5
    comparison = compare_spell_usage(
        spell=_spell(),
        presence=1.0,
        user_times=[10.5],
        ref_times=[10.5],
        n_usages_median=1.0,
        reference_n=20,
        slot_ref_times=[ref_dist],
    )
    assert len(comparison.step_grades) == len(comparison.alignment.steps)
    match_grades = [
        g
        for s, g in zip(comparison.alignment.steps, comparison.step_grades, strict=True)
        if s.kind is AlignmentKind.MATCH
    ]
    assert len(match_grades) == 1
    assert match_grades[0] is not None
    assert match_grades[0].grade == "green"


def test_step_grade_none_when_slot_ref_times_not_provided() -> None:
    comparison = compare_spell_usage(
        spell=_spell(),
        presence=1.0,
        user_times=[10.0],
        ref_times=[10.0],
        n_usages_median=1.0,
        reference_n=5,
    )
    match_grades = [g for g in comparison.step_grades if g is not None]
    assert len(match_grades) == 1
    assert match_grades[0].grade == "insufficient"  # empty reference distribution -> n=0 < 15


def test_thin_slot_distribution_grades_insufficient_not_red() -> None:
    """T2.3 acceptance, exercised through the real comparison pipeline:
    n=8 for a position must never be red.
    """
    thin_dist = [float(i) for i in range(8)]
    comparison = compare_spell_usage(
        spell=_spell(),
        presence=1.0,
        user_times=[20.0],  # far outside [0..7] — would be "red" with n>=15
        ref_times=[3.5],
        n_usages_median=1.0,
        reference_n=8,
        slot_ref_times=[thin_dist],
    )
    match_grade = next(g for g in comparison.step_grades if g is not None)
    assert match_grade.grade == "insufficient"


# -- T2.4: phase-partitioned alignment (achado 3.2, rotação-fantasma) --------------


def test_a_cast_never_pairs_with_an_expected_usage_from_another_phase() -> None:
    """T2.4 acceptance: a fight with (at least) 2 phases -> independent
    alignments per phase; the player's cast in phase 2 must match phase
    2's own expected usage, never phase 1's — even though both are
    numerically 10.0s relative to their own interval's start.
    """
    intervals = derive_phase_intervals(
        [{"id": 1, "startTime": 0}, {"id": 2, "startTime": 100}],
        fight_start_ms=0,
        fight_end_ms=200,
    )
    comparison = compare_spell_usage_by_phase(
        spell=_spell(),
        presence=1.0,
        user_phase_times={(2, 0): [10.0]},  # nothing cast in phase (1,0)
        ref_phase_times={(1, 0): [10.0], (2, 0): [10.0]},
        intervals=intervals,
        n_usages_median=1.0,
        reference_n=5,
    )
    assert comparison.alignment.n_matched == 1
    assert comparison.alignment.n_missed == 1
    assert comparison.alignment.n_extra == 0
    # Chronological merge order: phase (1,0)'s MISSED first, then phase
    # (2,0)'s MATCH — never a match "borrowed" from the wrong phase.
    kinds = [s.kind for s in comparison.alignment.steps]
    assert kinds == [AlignmentKind.MISSED, AlignmentKind.MATCH]


def test_phase_less_fight_matches_the_flat_alignment_exactly() -> None:
    """T2.4 acceptance: a fight without phases must produce an IDENTICAL
    result to the flat (pre-T2.4) alignment — no regression.
    """
    intervals = derive_phase_intervals([], fight_start_ms=0, fight_end_ms=400)
    user_times = [10.0, 40.0, 70.0]
    ref_times = [10.0, 40.0]

    phase_comparison = compare_spell_usage_by_phase(
        spell=_spell(),
        presence=0.8,
        user_phase_times={(0, 0): user_times},
        ref_phase_times={(0, 0): ref_times},
        intervals=intervals,
        n_usages_median=2.0,
        reference_n=8,
        flat_ref_times=ref_times,
    )
    flat_comparison = compare_spell_usage(
        spell=_spell(),
        presence=0.8,
        user_times=user_times,
        ref_times=ref_times,
        n_usages_median=2.0,
        reference_n=8,
    )
    assert phase_comparison.alignment == flat_comparison.alignment
    assert phase_comparison.cd_type == flat_comparison.cd_type


def test_reference_with_fewer_occurrences_contributes_only_to_the_phases_it_reached() -> None:
    """T2.4 point 6: a phase key present only in ref_phase_times (a faster
    reference kill never reached it, so the TARGET's own missing data for
    that key is fine) doesn't crash — it's just an all-MISSED sub-alignment
    if the player also has no casts there.
    """
    intervals = derive_phase_intervals(
        [{"id": 1, "startTime": 0}, {"id": 1, "startTime": 100}],
        fight_start_ms=0,
        fight_end_ms=200,
    )
    comparison = compare_spell_usage_by_phase(
        spell=_spell(),
        presence=1.0,
        user_phase_times={(1, 0): [10.0]},
        ref_phase_times={(1, 0): [10.0], (1, 1): [10.0]},  # only reached by some references
        intervals=intervals,
        n_usages_median=1.0,
        reference_n=5,
    )
    assert comparison.alignment.n_matched == 1  # (1,0)
    assert comparison.alignment.n_missed == 1  # (1,1): expected but the player never got there
