from __future__ import annotations

from itertools import count

import pytest

from botgitgud.analysis.profile import build_cd_reference_profile, discover_eligible_spell_ids
from botgitgud.domain.blacklist import MAJOR_CD_BLACKLIST
from botgitgud.domain.cooldowns import BASE_COOLDOWNS_S
from botgitgud.domain.models import (
    CollectionProvenance,
    CollectionStatus,
    FightRef,
    MeasurementProvenance,
    PlayerBuild,
    PlayerLog,
    SpellProfile,
)

_IDENTITIES = count(1)


def _log(
    duration_s: float,
    cast_timeline: dict[int, tuple[float, ...]],
    *,
    phase_cast_timeline: dict[int, dict[tuple[int, int], tuple[float, ...]]] | None = None,
) -> PlayerLog:
    fight = FightRef(
        report_code="ABCDEFGHIJKLMNOP",
        fight_id=next(_IDENTITIES),
        encounter_id=3179,
        boss_name="Fallen-King Salhadaar",
        difficulty=5,
        duration_s=duration_s,
        kill=True,
    )
    build = PlayerBuild(
        character_name="Ref",
        server="Azralon",
        class_name="Warlock",
        spec_name="Demonology",
        role="dps",
        item_level=283.0,
        talent_hash=None,
        tier_pieces=None,
    )
    return PlayerLog(
        fight=fight,
        build=build,
        dps=100000.0,
        percentile=50.0,
        cast_timeline=cast_timeline,
        phase_cast_timeline=phase_cast_timeline or {},
        measurement_provenance=MeasurementProvenance(
            casts_collection=CollectionProvenance(
                CollectionStatus.COMPLETE, (), 0, duration_s * 1000
            )
        ),
    )


def test_empty_reference_logs_returns_empty_profile() -> None:
    profile, n_positional = build_cd_reference_profile([], target_duration_s=300.0)
    assert profile == {}
    assert n_positional == 0


def test_presence_counts_across_the_whole_sanity_band_pool() -> None:
    logs = [
        _log(300.0, {1: (10.0,)}),
        _log(300.0, {1: (10.0,)}),
        _log(300.0, {}),
        _log(300.0, {}),
    ]
    profile, _n = build_cd_reference_profile(logs, target_duration_s=300.0)
    assert profile[1].presence == 0.5


def test_n_usages_median_at_matching_duration_equals_raw_count_median() -> None:
    """When every positional log's duration equals target_duration_s, the
    rate/minute normalization is a no-op — this is the easiest case to
    hand-verify: median(2, 4) usages -> 3.0.
    """
    logs = [_log(300.0, {1: (10.0, 40.0)}), _log(300.0, {1: (10.0, 40.0, 70.0, 100.0)})]
    profile, n_positional = build_cd_reference_profile(logs, target_duration_s=300.0)
    assert n_positional == 2
    assert profile[1].n_usages_median == pytest.approx(3.0)


def test_n_usages_median_pads_zero_for_positional_logs_that_never_cast_it() -> None:
    """A positional log that never cast the spell still contributes an
    explicit rate of 0.0 to the median — omitting it would silently bias
    the median upward.
    """
    logs = [
        _log(300.0, {1: (10.0, 40.0)}),  # 2 casts
        _log(300.0, {1: (10.0, 40.0, 70.0, 100.0)}),  # 4 casts
        _log(300.0, {}),  # 0 casts of spell 1
    ]
    profile, n_positional = build_cd_reference_profile(logs, target_duration_s=300.0)
    assert n_positional == 3
    assert profile[1].n_usages_median == pytest.approx(3.0)  # median(2, 4); absence excluded
    assert profile[1].n_positional_with_spell == 2


def test_ref_times_are_per_slot_medians_from_positional_band_only() -> None:
    logs = [
        _log(300.0, {1: (10.0, 40.0)}),
        _log(300.0, {1: (12.0, 44.0)}),
        _log(600.0, {1: (500.0, 550.0)}),  # far outside ±12% positional band — excluded
    ]
    profile, n_positional = build_cd_reference_profile(logs, target_duration_s=300.0)
    assert n_positional == 2
    assert profile[1].ref_times == (11.0, 42.0)


def test_out_of_positional_band_logs_still_count_toward_presence() -> None:
    logs = [_log(300.0, {1: (10.0,)}), _log(600.0, {1: (500.0,)})]
    profile, n_positional = build_cd_reference_profile(logs, target_duration_s=300.0)
    assert profile[1].presence == 1.0  # both logs counted for presence
    assert n_positional == 1  # only the near-duration one counts for timing/count


def test_zero_positional_logs_yields_zero_n_usages_median() -> None:
    logs = [_log(600.0, {1: (10.0,)})]  # outside the positional band of target
    profile, n_positional = build_cd_reference_profile(logs, target_duration_s=300.0)
    assert n_positional == 0
    assert profile[1].n_usages_median == 0.0
    assert profile[1].ref_times == ()


# -- T2.4: phase-keyed profile ---------------------------------------------------


def test_phase_ref_times_keeps_occurrences_separate() -> None:
    """T2.4 acceptance: casts from different occurrences of the same
    phase_id never get averaged/merged together.
    """
    logs = [
        _log(
            300.0,
            {1: (10.0, 60.0)},
            phase_cast_timeline={1: {(1, 0): (10.0,), (1, 1): (60.0,)}},
        ),
        _log(
            300.0,
            {1: (12.0, 62.0)},
            phase_cast_timeline={1: {(1, 0): (12.0,), (1, 1): (62.0,)}},
        ),
    ]
    profile, _n = build_cd_reference_profile(logs, target_duration_s=300.0)
    sp = profile[1]
    assert sp.phase_ref_times[(1, 0)] == (11.0,)
    assert sp.phase_ref_times[(1, 1)] == (61.0,)


def test_reference_with_fewer_phase_occurrences_only_contributes_to_intervals_it_has() -> None:
    """T2.4 point 6: a faster kill (fewer cycles) contributes only to the
    intervals it actually reached — never padded/faked for the late ones.
    """
    logs = [
        _log(
            300.0,
            {1: (10.0, 60.0, 110.0)},
            phase_cast_timeline={1: {(1, 0): (10.0,), (2, 0): (60.0,), (1, 1): (110.0,)}},
        ),
        _log(  # faster kill: never reached (1,1)
            280.0,
            {1: (10.0, 60.0)},
            phase_cast_timeline={1: {(1, 0): (10.0,), (2, 0): (60.0,)}},
        ),
    ]
    profile, _n = build_cd_reference_profile(logs, target_duration_s=300.0)
    sp = profile[1]
    assert len(sp.phase_ref_times[(1, 0)]) == 1  # one median value, from 2 contributing logs
    assert len(sp.phase_slot_ref_times[(1, 0)][0]) == 2  # both logs contributed here
    assert len(sp.phase_slot_ref_times[(1, 1)][0]) == 1  # only the slower log reached (1,1)


def test_phase_slot_ref_times_holds_the_raw_distribution_per_phase_key() -> None:
    logs = [
        _log(300.0, {1: (10.0,)}, phase_cast_timeline={1: {(1, 0): (10.0,)}}),
        _log(300.0, {1: (14.0,)}, phase_cast_timeline={1: {(1, 0): (14.0,)}}),
        _log(300.0, {1: (18.0,)}, phase_cast_timeline={1: {(1, 0): (18.0,)}}),
    ]
    profile, _n = build_cd_reference_profile(logs, target_duration_s=300.0)
    sp = profile[1]
    assert set(sp.phase_slot_ref_times[(1, 0)][0]) == {10.0, 14.0, 18.0}
    assert sp.phase_ref_times[(1, 0)] == (14.0,)  # median of the raw distribution


def test_no_phase_cast_timeline_yields_empty_phase_ref_times() -> None:
    """Backward compatibility: a log with no phase_cast_timeline (the
    zero-value default) contributes nothing to the phase-keyed profile,
    without crashing.
    """
    logs = [_log(300.0, {1: (10.0,)})]  # phase_cast_timeline defaults to {}
    profile, _n = build_cd_reference_profile(logs, target_duration_s=300.0)
    assert profile[1].phase_ref_times == {}
    assert profile[1].phase_slot_ref_times == {}


# -- discover_eligible_spell_ids ------------------------------------------------


def test_low_presence_spell_is_excluded() -> None:
    profile = {
        1: SpellProfile(spell_id=1, presence=0.5, ref_times=(10.0, 200.0), n_usages_median=2.0)
    }
    assert discover_eligible_spell_ids(profile) == []


# -- EC.1: n_with_spell (absolute subgroup size, presence's numerator) ------------


def test_n_with_spell_is_presence_count_the_numerator_of_presence() -> None:
    logs = [_log(300.0, {1: (10.0,)}), _log(300.0, {1: (10.0,)}), _log(300.0, {}), _log(300.0, {})]
    profile, _n = build_cd_reference_profile(logs, target_duration_s=300.0)
    assert profile[1].n_with_spell == 2
    assert profile[1].presence == 0.5


def test_low_ratio_presence_with_large_absolute_subgroup_is_still_eligible() -> None:
    """EC.1 regression: a minority-build spell (low ratio across a large,
    mixed cohort) must not disappear when the absolute subgroup that casts
    it is large enough to be real evidence — this is exactly what protects
    against the bug once EC.3 removes talent_cluster from matching.
    """
    logs = [_log(300.0, {1: (10.0, 40.0, 70.0)}) for _ in range(10)] + [
        _log(300.0, {}) for _ in range(90)
    ]
    profile, _n = build_cd_reference_profile(logs, target_duration_s=300.0)
    assert profile[1].presence == pytest.approx(0.10)  # abaixo do piso de razão (0.70)
    assert profile[1].n_with_spell == 10  # mas acima do piso absoluto (8)
    assert 1 in discover_eligible_spell_ids(profile)


def test_low_ratio_presence_with_small_absolute_subgroup_is_still_excluded() -> None:
    logs = [_log(300.0, {1: (10.0, 40.0, 70.0)}) for _ in range(3)] + [
        _log(300.0, {}) for _ in range(97)
    ]
    profile, _n = build_cd_reference_profile(logs, target_duration_s=300.0)
    assert profile[1].n_with_spell == 3  # abaixo do piso absoluto também
    assert 1 not in discover_eligible_spell_ids(profile)


def test_blacklisted_spell_is_excluded_even_with_high_presence() -> None:
    blacklisted_id = next(iter(MAJOR_CD_BLACKLIST))
    profile = {
        blacklisted_id: SpellProfile(
            spell_id=blacklisted_id, presence=1.0, ref_times=(10.0, 200.0), n_usages_median=2.0
        )
    }
    assert discover_eligible_spell_ids(profile) == []


def test_eligible_spells_ordered_by_descending_presence() -> None:
    profile = {
        1: SpellProfile(spell_id=1, presence=0.8, ref_times=(10.0, 200.0), n_usages_median=2.0),
        2: SpellProfile(spell_id=2, presence=1.0, ref_times=(10.0, 200.0), n_usages_median=2.0),
        3: SpellProfile(spell_id=3, presence=0.9, ref_times=(10.0, 200.0), n_usages_median=2.0),
    }
    assert discover_eligible_spell_ids(profile) == [2, 3, 1]


def test_ties_broken_by_ascending_spell_id_deterministically() -> None:
    profile = {
        20: SpellProfile(spell_id=20, presence=1.0, ref_times=(10.0, 200.0), n_usages_median=2.0),
        10: SpellProfile(spell_id=10, presence=1.0, ref_times=(10.0, 200.0), n_usages_median=2.0),
    }
    assert discover_eligible_spell_ids(profile) == [10, 20]


def test_a_curated_base_cooldown_takes_priority_over_the_observed_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T2.5 acceptance: a known base_cooldown_s uses the FIRST branch of
    the classification/eligibility rule — here, a curated cooldown below
    the 15s eligibility floor excludes the spell even though its observed
    interval (210s) alone would look like a perfectly legitimate long CD.
    """
    spell_id = 424242
    monkeypatch.setitem(BASE_COOLDOWNS_S, spell_id, 5.0)  # below _MIN_ELIGIBLE_INTERVAL_S
    profile = {
        spell_id: SpellProfile(
            spell_id=spell_id, presence=1.0, ref_times=(10.0, 220.0), n_usages_median=2.0
        )
    }
    assert discover_eligible_spell_ids(profile) == []


def test_without_a_curated_cooldown_the_observed_interval_branch_still_governs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T2.5 acceptance: a spell with no curated data degrades to the
    existing observed-interval branch without error — same spell, same
    data, absent from the curated table this time.
    """
    spell_id = 424243
    monkeypatch.delitem(BASE_COOLDOWNS_S, spell_id, raising=False)
    profile = {
        spell_id: SpellProfile(
            spell_id=spell_id, presence=1.0, ref_times=(10.0, 220.0), n_usages_median=2.0
        )
    }
    assert discover_eligible_spell_ids(profile) == [spell_id]  # 210s interval >= 15s -> eligible
