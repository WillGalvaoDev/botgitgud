from __future__ import annotations

import random

import pytest

from botgitgud.analysis.grading import (
    BOOTSTRAP_SEED,
    FDR,
    MIN_N_FOR_GRADING,
    N_BOOTSTRAP,
    benjamini_hochberg,
    bootstrap_median_ci,
    compute_quantile_stats,
    empirical_quantile,
    grade_deviation,
    grade_from_quantile,
    two_tailed_p_value,
)

# 20 evenly-spaced reference times, easy to reason about quantiles for.
_REF_20 = [float(i) for i in range(1, 21)]  # 1..20, median=10.5


# -- empirical_quantile / grade_from_quantile --------------------------------------


def test_player_exactly_at_the_median_is_green_regardless_of_absolute_value() -> None:
    """T2.3 acceptance: exact-median player -> green, independent of scale."""
    short_cd = [10.0, 20.0, 30.0, 40.0, 50.0] * 4  # median 30, small scale
    long_cd = [100.0, 200.0, 300.0, 400.0, 500.0] * 4  # median 300, large scale

    assert grade_deviation(30.0, short_cd) == "green"
    assert grade_deviation(300.0, long_cd) == "green"


def test_same_absolute_delta_grades_differently_on_short_vs_long_cd() -> None:
    """T2.3 acceptance: 5s off a short CD vs. a long CD must NOT grade the
    same — the whole point of quantile-relative grading.
    """
    short_cd = [25.0, 28.0, 30.0, 32.0, 35.0] * 4  # tight spread around 30
    long_cd = [250.0, 280.0, 300.0, 320.0, 350.0] * 4  # same shape, 10x scale

    short_grade = grade_deviation(35.0, short_cd)  # +5s on a ~30s CD: big relative move
    long_grade = grade_deviation(305.0, long_cd)  # +5s on a ~300s CD: tiny relative move

    assert short_grade != long_grade


def test_empirical_quantile_of_the_minimum_is_near_zero() -> None:
    q = empirical_quantile(1.0, _REF_20)
    assert q is not None
    assert q < 0.10


def test_empirical_quantile_of_the_maximum_is_near_one() -> None:
    q = empirical_quantile(20.0, _REF_20)
    assert q is not None
    assert q > 0.90


def test_empirical_quantile_none_for_empty_reference() -> None:
    assert empirical_quantile(10.0, []) is None


def test_grade_from_quantile_boundaries() -> None:
    assert grade_from_quantile(0.5) == "green"
    assert grade_from_quantile(0.25) == "green"
    assert grade_from_quantile(0.75) == "green"
    assert grade_from_quantile(0.24) == "yellow"
    assert grade_from_quantile(0.76) == "yellow"
    assert grade_from_quantile(0.10) == "yellow"
    assert grade_from_quantile(0.90) == "yellow"
    assert grade_from_quantile(0.09) == "red"
    assert grade_from_quantile(0.91) == "red"


def test_n_below_threshold_is_insufficient_never_red() -> None:
    """T2.3 acceptance: n=8 for a position -> insufficient, never red —
    even for an extreme-looking value.
    """
    thin_ref = [float(i) for i in range(8)]  # n=8 < MIN_N_FOR_GRADING
    assert grade_deviation(1000.0, thin_ref) == "insufficient"
    assert grade_deviation(0.0, thin_ref) == "insufficient"


def test_n_at_threshold_is_graded_normally() -> None:
    ref = [float(i) for i in range(MIN_N_FOR_GRADING)]
    assert grade_deviation(ref[len(ref) // 2], ref) != "insufficient"


# -- compute_quantile_stats ---------------------------------------------------------


def test_quantile_stats_empty() -> None:
    stats = compute_quantile_stats([])
    assert stats.n == 0
    assert stats.p50 is None


def test_quantile_stats_single_value() -> None:
    stats = compute_quantile_stats([42.0])
    assert stats.n == 1
    assert stats.p10 == stats.p50 == stats.p90 == 42.0


def test_quantile_stats_median_matches_statistics_median() -> None:
    import statistics

    stats = compute_quantile_stats(_REF_20)
    assert stats.n == 20
    assert stats.p50 == statistics.median(_REF_20)


def test_quantile_stats_ordering_p10_le_p50_le_p90() -> None:
    stats = compute_quantile_stats(_REF_20)
    assert stats.p10 is not None
    assert stats.p90 is not None
    assert stats.p10 <= stats.p50 <= stats.p90  # type: ignore[operator]


# -- bootstrap_median_ci: T2.3 acceptance --------------------------------------------


def test_bootstrap_ci_is_deterministic_across_runs() -> None:
    ci_a = bootstrap_median_ci(_REF_20)
    ci_b = bootstrap_median_ci(_REF_20)
    assert ci_a == ci_b


def test_bootstrap_ci_uses_the_documented_seed_and_n_bootstrap() -> None:
    assert BOOTSTRAP_SEED == 20260817
    assert N_BOOTSTRAP == 2000


def test_bootstrap_ci_never_touches_the_global_random_module_state() -> None:
    """A fixed local Random instance must not perturb unrelated global
    `random` calls elsewhere in the process.
    """
    random.seed(1)
    before = random.random()
    bootstrap_median_ci(_REF_20)
    random.seed(1)
    after = random.random()
    assert before == after


def test_bootstrap_ci_brackets_the_true_median() -> None:
    ci = bootstrap_median_ci(_REF_20)
    assert ci is not None
    lo, hi = ci
    assert lo <= 10.5 <= hi


def test_bootstrap_ci_none_for_empty_input() -> None:
    assert bootstrap_median_ci([]) is None


def test_bootstrap_ci_single_value_is_degenerate() -> None:
    assert bootstrap_median_ci([7.0]) == (7.0, 7.0)


# -- Benjamini-Hochberg: T2.3 acceptance ----------------------------------------------


def test_benjamini_hochberg_empty_input() -> None:
    assert benjamini_hochberg([]) == []


def test_benjamini_hochberg_all_significant_when_all_p_values_tiny() -> None:
    p_values = [0.001] * 10
    assert all(benjamini_hochberg(p_values, fdr=0.10))


def test_benjamini_hochberg_none_significant_when_all_p_values_large() -> None:
    p_values = [0.9] * 10
    assert not any(benjamini_hochberg(p_values, fdr=0.10))


def test_benjamini_hochberg_controls_false_discovery_rate_under_the_null() -> None:
    """T2.3 acceptance: with 100 random deviations under the null
    hypothesis (p-values ~ Uniform(0,1)), at most ~10% survive BH at
    FDR=0.10. Fixed seed for a non-flaky test; BH's guarantee is on
    *expected* FDR, so this checks a generous bound, not an exact count.
    """
    rng = random.Random(42)
    p_values = [rng.random() for _ in range(100)]

    survives = benjamini_hochberg(p_values, fdr=FDR)

    assert sum(survives) <= 15  # generous margin around the 10% expectation


def test_benjamini_hochberg_survivors_have_the_smallest_p_values() -> None:
    p_values = [0.5, 0.001, 0.9, 0.002, 0.4]
    survives = benjamini_hochberg(p_values, fdr=0.10)
    survivor_p = {p_values[i] for i, s in enumerate(survives) if s}
    non_survivor_p = {p_values[i] for i, s in enumerate(survives) if not s}
    if survivor_p and non_survivor_p:
        assert max(survivor_p) <= min(non_survivor_p)


# -- two_tailed_p_value --------------------------------------------------------------


def test_two_tailed_p_value_at_median_is_one() -> None:
    assert two_tailed_p_value(0.5) == 1.0


def test_two_tailed_p_value_at_extremes_is_zero() -> None:
    assert two_tailed_p_value(0.0) == 0.0
    assert two_tailed_p_value(1.0) == 0.0


def test_two_tailed_p_value_symmetric() -> None:
    assert two_tailed_p_value(0.1) == pytest.approx(two_tailed_p_value(0.9))
