from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from botgitgud.analysis.alignment import AlignmentKind, align

GAP_PENALTY = 25.0


def _sorted_floats(max_size: int = 12) -> st.SearchStrategy[list[float]]:
    return st.lists(
        st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
        max_size=max_size,
    ).map(sorted)


# -- explicit regression test: the exact bug from relario.md achado 3.1 -----


def test_missed_casts_are_reported() -> None:
    user = [10.0, 130.0]  # 2 usos
    ref = [10.0, 130.0, 250.0, 370.0]  # coorte usa 4
    a = align(user, ref)
    assert a.n_matched == 2
    assert a.n_missed == 2  # o legacy reportava 0
    assert [s.ref_time for s in a.steps if s.kind is AlignmentKind.MISSED] == [250.0, 370.0]


# -- required properties (Hypothesis) ----------------------------------------


@given(x=_sorted_floats())
def test_identity(x: list[float]) -> None:
    a = align(x, x)
    assert a.total_cost == 0.0
    assert all(s.kind is AlignmentKind.MATCH for s in a.steps)


@given(u=_sorted_floats(), r=_sorted_floats())
def test_completeness(u: list[float], r: list[float]) -> None:
    a = align(u, r)
    assert a.n_matched + a.n_extra == len(u)
    assert a.n_matched + a.n_missed == len(r)


@given(u=_sorted_floats(), r=_sorted_floats())
def test_monotonicity_of_matched_indices(u: list[float], r: list[float]) -> None:
    a = align(u, r)
    match_steps = [s for s in a.steps if s.kind is AlignmentKind.MATCH]
    user_indices: list[int] = []
    ref_indices: list[int] = []
    for s in match_steps:
        assert s.user_index is not None
        assert s.ref_index is not None
        user_indices.append(s.user_index)
        ref_indices.append(s.ref_index)
    assert user_indices == sorted(user_indices)
    assert len(set(user_indices)) == len(user_indices)  # strictly increasing
    assert ref_indices == sorted(ref_indices)
    assert len(set(ref_indices)) == len(ref_indices)


@given(r=_sorted_floats())
def test_empty_user_sequence_is_all_missed(r: list[float]) -> None:
    a = align([], r)
    assert a.n_missed == len(r)
    assert a.n_matched == 0
    assert a.n_extra == 0
    assert all(s.kind is AlignmentKind.MISSED for s in a.steps)
    assert [s.ref_time for s in a.steps] == r


@given(u=_sorted_floats())
def test_empty_ref_sequence_is_all_extra(u: list[float]) -> None:
    a = align(u, [])
    assert a.n_extra == len(u)
    assert a.n_matched == 0
    assert a.n_missed == 0
    assert all(s.kind is AlignmentKind.EXTRA for s in a.steps)
    assert [s.user_time for s in a.steps] == u


@given(u=_sorted_floats(), r=_sorted_floats())
def test_cost_symmetry(u: list[float], r: list[float]) -> None:
    assert align(u, r).total_cost == align(r, u).total_cost


@given(u=_sorted_floats(), r=_sorted_floats())
def test_determinism(u: list[float], r: list[float]) -> None:
    a1 = align(u, r)
    a2 = align(u, r)
    assert a1.steps == a2.steps
    assert a1.total_cost == a2.total_cost


# -- input validation ---------------------------------------------------------


def test_unsorted_user_times_raises() -> None:
    with pytest.raises(ValueError, match="user_times"):
        align([10.0, 5.0], [1.0, 2.0])


def test_unsorted_ref_times_raises() -> None:
    with pytest.raises(ValueError, match="ref_times"):
        align([1.0, 2.0], [10.0, 5.0])


def test_both_empty_returns_zero_cost_no_steps() -> None:
    a = align([], [])
    assert a.total_cost == 0.0
    assert a.steps == ()
    assert (a.n_matched, a.n_missed, a.n_extra) == (0, 0, 0)


# -- tie-break precedence: MATCH > MISSED > EXTRA ----------------------------


def test_tie_break_prefers_match_over_gap_ops() -> None:
    # user=[0.0], ref=[0.0]: match cost 0 vs (missed+extra)=2*gap_penalty.
    # Any gap_penalty > 0 makes MATCH strictly cheaper here, but this also
    # documents the intended precedence for equal-cost ties in general.
    a = align([0.0], [0.0], gap_penalty=GAP_PENALTY)
    assert a.n_matched == 1
    assert a.steps[0].kind is AlignmentKind.MATCH


def test_gap_penalty_is_configurable() -> None:
    # A cast 100s away from the only reference time is cheaper as
    # MISSED + EXTRA (2 * gap_penalty = 20) than as a MATCH (cost 100)
    # when gap_penalty is small.
    a = align([100.0], [0.0], gap_penalty=10.0)
    assert a.n_matched == 0
    assert a.n_missed == 1
    assert a.n_extra == 1
