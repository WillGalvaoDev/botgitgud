from __future__ import annotations

from botgitgud.analysis.talent_cluster import (
    JACCARD_THRESHOLD,
    analyze_build_divergence,
    cluster_builds,
    diff_talent_pairs,
    jaccard_similarity,
)
from botgitgud.domain.models import FightRef, PlayerBuild, PlayerLog

BUILD_A: frozenset[tuple[int, int]] = frozenset({(1, 1), (2, 1), (3, 2), (4, 1)})
BUILD_B: frozenset[tuple[int, int]] = frozenset({(9, 1), (10, 2), (11, 1), (12, 1)})  # disjoint


def _log(
    *, talent_pairs: frozenset[tuple[int, int]], dps: float | None = 100_000.0, name: str = "Ref"
) -> PlayerLog:
    fight = FightRef(
        report_code="ABCDEFGHIJKLMNOP",
        fight_id=1,
        encounter_id=3179,
        boss_name="Fallen-King Salhadaar",
        difficulty=5,
        duration_s=300.0,
        kill=True,
    )
    build = PlayerBuild(
        character_name=name,
        server="Azralon",
        class_name="Warlock",
        spec_name="Demonology",
        role="dps",
        item_level=283.0,
        talent_hash=None,
        tier_pieces=4,
        talent_pairs=talent_pairs,
    )
    return PlayerLog(fight=fight, build=build, dps=dps, percentile=50.0, cast_timeline={})


# -- jaccard_similarity -----------------------------------------------------------


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


# -- cluster_builds: T2.2 acceptance criteria --------------------------------------


def test_two_clearly_distinct_builds_form_two_clusters() -> None:
    logs = [_log(talent_pairs=BUILD_A) for _ in range(5)] + [
        _log(talent_pairs=BUILD_B) for _ in range(3)
    ]
    clusters = cluster_builds(logs)

    assert len(clusters) == 2
    assert clusters[0].cluster_id == 0
    assert len(clusters[0].members) == 5  # largest first
    assert len(clusters[1].members) == 3
    assert all(m.build.talent_pairs == BUILD_A for m in clusters[0].members)
    assert all(m.build.talent_pairs == BUILD_B for m in clusters[1].members)


def test_identical_builds_form_a_single_cluster() -> None:
    logs = [_log(talent_pairs=BUILD_A) for _ in range(6)]
    clusters = cluster_builds(logs)

    assert len(clusters) == 1
    assert len(clusters[0].members) == 6


def test_logs_without_talent_data_are_excluded_from_clustering() -> None:
    logs = [_log(talent_pairs=BUILD_A) for _ in range(4)] + [
        _log(talent_pairs=frozenset()) for _ in range(2)
    ]
    clusters = cluster_builds(logs)

    assert len(clusters) == 1
    assert len(clusters[0].members) == 4


def test_near_but_not_similar_enough_builds_form_separate_clusters() -> None:
    """Two builds sharing 3/5 nodes (0.6 similarity) — below the 0.85
    threshold — must NOT merge.
    """
    a = frozenset({(1, 1), (2, 1), (3, 1), (4, 1), (5, 1)})
    b = frozenset({(1, 1), (2, 1), (3, 1), (6, 1), (7, 1)})
    assert jaccard_similarity(a, b) < JACCARD_THRESHOLD

    logs = [_log(talent_pairs=a) for _ in range(3)] + [_log(talent_pairs=b) for _ in range(3)]
    clusters = cluster_builds(logs)

    assert len(clusters) == 2


# -- diff_talent_pairs --------------------------------------------------------------


def test_diff_talent_pairs_reports_only_differing_nodes() -> None:
    dominant = frozenset({(1, 1), (2, 1), (3, 2)})
    player = frozenset({(1, 1), (2, 2), (4, 1)})  # node 2 differs, node 3 vs 4 differ

    diffs = diff_talent_pairs(dominant, player)
    node_ids = {d.node_id for d in diffs}

    assert node_ids == {2, 3, 4}
    assert 1 not in node_ids  # identical pick, not a difference


def test_diff_talent_pairs_empty_when_sets_equal() -> None:
    assert diff_talent_pairs(BUILD_A, BUILD_A) == []


# -- analyze_build_divergence: T2.2 acceptance criteria ------------------------------


def test_minority_build_player_gets_a_divergence_finding() -> None:
    target = _log(talent_pairs=BUILD_B, dps=1_090_000.0, name="Target")
    dominant_cohort = [_log(talent_pairs=BUILD_A, dps=1_240_000.0) for _ in range(32)]
    minority_cohort = [_log(talent_pairs=BUILD_B, dps=1_090_000.0) for _ in range(1)]

    divergence = analyze_build_divergence(target, dominant_cohort + minority_cohort)

    assert divergence is not None
    assert divergence.player_cluster_n == 2  # target + the 1 matching cohort member
    assert divergence.total_n == 34
    assert divergence.dominant_cluster_n == 32
    assert divergence.player_pct < 0.20
    assert divergence.dominant_median_dps == 1_240_000.0
    assert divergence.player_median_dps == 1_090_000.0
    assert len(divergence.differences) > 0


def test_identical_builds_across_cohort_yields_no_divergence() -> None:
    target = _log(talent_pairs=BUILD_A, name="Target")
    cohort = [_log(talent_pairs=BUILD_A) for _ in range(20)]

    assert analyze_build_divergence(target, cohort) is None


def test_dominant_build_player_gets_no_divergence_finding() -> None:
    target = _log(talent_pairs=BUILD_A, name="Target")
    cohort = [_log(talent_pairs=BUILD_A) for _ in range(30)] + [
        _log(talent_pairs=BUILD_B) for _ in range(2)
    ]

    assert analyze_build_divergence(target, cohort) is None


def test_no_divergence_when_player_share_is_at_or_above_minority_threshold() -> None:
    """21/100 (>= 20%) is still a minority vs. the dominant cluster, but
    not the "< 20%" trigger the report finding requires.
    """
    target = _log(talent_pairs=BUILD_B, name="Target")
    dominant_cohort = [_log(talent_pairs=BUILD_A) for _ in range(79)]
    minority_cohort = [_log(talent_pairs=BUILD_B) for _ in range(20)]

    divergence = analyze_build_divergence(target, dominant_cohort + minority_cohort)

    assert divergence is None


def test_no_divergence_when_target_has_no_talent_data() -> None:
    target = _log(talent_pairs=frozenset(), name="Target")
    cohort = [_log(talent_pairs=BUILD_A) for _ in range(20)]

    assert analyze_build_divergence(target, cohort) is None
