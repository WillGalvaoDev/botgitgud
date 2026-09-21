from __future__ import annotations

from itertools import pairwise

from botgitgud.analysis.phases import (
    FALLBACK_PHASE_ID,
    derive_phase_intervals,
    find_interval,
)

# docs/schema_confirmado.md §7: fights[].phaseTransitions for the real
# Zarad fixture (report PtfBbQKRY9d6zAMC, fight 1), verified live.
ZARAD_PHASE_TRANSITIONS = [
    {"id": 1, "startTime": 1026037},
    {"id": 2, "startTime": 1128366},
    {"id": 1, "startTime": 1148363},
    {"id": 2, "startTime": 1249977},
    {"id": 1, "startTime": 1269978},
]
ZARAD_FIGHT_START = 1026037
ZARAD_FIGHT_END = 1371183


def test_zarad_fixture_produces_exactly_five_intervals_with_the_documented_keys() -> None:
    """T2.4 acceptance: the real fight produces exactly 5 intervals with
    keys (1,0), (2,0), (1,1), (2,1), (1,2).
    """
    intervals = derive_phase_intervals(
        ZARAD_PHASE_TRANSITIONS, fight_start_ms=ZARAD_FIGHT_START, fight_end_ms=ZARAD_FIGHT_END
    )
    assert len(intervals) == 5
    assert [iv.key for iv in intervals] == [(1, 0), (2, 0), (1, 1), (2, 1), (1, 2)]


def test_zarad_fixture_interval_boundaries_are_contiguous() -> None:
    intervals = derive_phase_intervals(
        ZARAD_PHASE_TRANSITIONS, fight_start_ms=ZARAD_FIGHT_START, fight_end_ms=ZARAD_FIGHT_END
    )
    assert intervals[0].start_ms == ZARAD_FIGHT_START
    assert intervals[-1].end_ms == ZARAD_FIGHT_END
    for a, b in pairwise(intervals):
        assert a.end_ms == b.start_ms  # no gaps, no overlaps


def test_a_cast_in_the_third_occurrence_of_phase_1_is_never_confused_with_the_first() -> None:
    """T2.4 acceptance: a cast in the 3rd occurrence of phase 1 cannot be
    aggregated with casts from the 1st occurrence.
    """
    intervals = derive_phase_intervals(
        ZARAD_PHASE_TRANSITIONS, fight_start_ms=ZARAD_FIGHT_START, fight_end_ms=ZARAD_FIGHT_END
    )
    first_occurrence_ts = 1030000  # inside (1,0): [1026037, 1128366)
    third_occurrence_ts = 1300000  # inside (1,2): [1269978, 1371183)

    first_hit = find_interval(intervals, first_occurrence_ts)
    third_hit = find_interval(intervals, third_occurrence_ts)

    assert first_hit is not None
    assert third_hit is not None
    assert first_hit.key == (1, 0)
    assert third_hit.key == (1, 2)
    assert first_hit.key != third_hit.key


def test_fight_without_phases_falls_back_to_a_single_interval() -> None:
    """T2.4's mandatory fallback: no phase data -> one interval spanning
    the whole fight, "o comportamento degrada para o da Fase 0 sem
    quebrar."
    """
    intervals = derive_phase_intervals([], fight_start_ms=100.0, fight_end_ms=500.0)
    assert len(intervals) == 1
    assert intervals[0].phase_id == FALLBACK_PHASE_ID
    assert intervals[0].occurrence == 0
    assert intervals[0].start_ms == 100.0
    assert intervals[0].end_ms == 500.0


def test_three_phase_synthetic_fight_derives_three_distinct_intervals() -> None:
    transitions = [
        {"id": 10, "startTime": 0},
        {"id": 20, "startTime": 100},
        {"id": 30, "startTime": 250},
    ]
    intervals = derive_phase_intervals(transitions, fight_start_ms=0, fight_end_ms=400)
    assert [iv.key for iv in intervals] == [(10, 0), (20, 0), (30, 0)]
    assert intervals[0].start_ms == 0 and intervals[0].end_ms == 100
    assert intervals[1].start_ms == 100 and intervals[1].end_ms == 250
    assert intervals[2].start_ms == 250 and intervals[2].end_ms == 400


def test_find_interval_none_for_a_timestamp_outside_every_interval() -> None:
    intervals = derive_phase_intervals([], fight_start_ms=100.0, fight_end_ms=500.0)
    assert find_interval(intervals, 50.0) is None  # before the fight even starts


def test_find_interval_inclusive_on_the_very_last_boundary() -> None:
    intervals = derive_phase_intervals(
        ZARAD_PHASE_TRANSITIONS, fight_start_ms=ZARAD_FIGHT_START, fight_end_ms=ZARAD_FIGHT_END
    )
    hit = find_interval(intervals, ZARAD_FIGHT_END)
    assert hit is not None
    assert hit.key == (1, 2)


def test_find_interval_exact_boundary_belongs_to_the_later_interval() -> None:
    intervals = derive_phase_intervals(
        ZARAD_PHASE_TRANSITIONS, fight_start_ms=ZARAD_FIGHT_START, fight_end_ms=ZARAD_FIGHT_END
    )
    hit = find_interval(intervals, 1128366)  # exactly where (2,0) begins
    assert hit is not None
    assert hit.key == (2, 0)
