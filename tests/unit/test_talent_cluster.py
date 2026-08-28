from __future__ import annotations

from botgitgud.analysis.talent_cluster import JACCARD_THRESHOLD, jaccard_similarity

BUILD_A: frozenset[tuple[int, int]] = frozenset({(1, 1), (2, 1), (3, 2), (4, 1)})
BUILD_B: frozenset[tuple[int, int]] = frozenset({(9, 1), (10, 2), (11, 1), (12, 1)})  # disjoint

# -- jaccard_similarity -----------------------------------------------------------
#
# EC.4: `cluster_builds`/`diff_talent_pairs`/`analyze_build_divergence`
# (and their tests) were removed along with `BuildDivergence` — see
# analysis/talent_cluster.py's module docstring for why. `jaccard_similarity`
# remains: `cohort_match.py`'s v1 matching policy still uses it.


def test_jaccard_identical_sets_is_one() -> None:
    assert jaccard_similarity(BUILD_A, BUILD_A) == 1.0


def test_jaccard_disjoint_sets_is_zero() -> None:
    assert jaccard_similarity(BUILD_A, BUILD_B) == 0.0


def test_jaccard_partial_overlap() -> None:
    a = frozenset({(1, 1), (2, 1), (3, 1)})
    b = frozenset({(1, 1), (2, 1), (4, 1)})
    # intersection {(1,1),(2,1)}=2, union {(1,1),(2,1),(3,1),(4,1)}=4
    assert jaccard_similarity(a, b) == 0.5


def test_jaccard_both_empty_is_one() -> None:
    assert jaccard_similarity(frozenset(), frozenset()) == 1.0


def test_jaccard_one_empty_is_zero() -> None:
    assert jaccard_similarity(BUILD_A, frozenset()) == 0.0


def test_jaccard_threshold_is_still_085() -> None:
    """`cohort_match.py`'s v1 policy imports this constant directly —
    guard against an accidental drift."""
    assert JACCARD_THRESHOLD == 0.85
