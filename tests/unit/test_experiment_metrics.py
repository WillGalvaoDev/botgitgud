from __future__ import annotations

import pytest

from botgitgud.domain.specs import SpecId
from botgitgud.phase4.experiment_metrics import (
    ArchitectureComparison,
    MedianBaseline,
    evaluate_split,
    regression_metrics,
    spearman,
)
from botgitgud.phase4.experimental_dataset import ExperimentalObservation
from botgitgud.phase4.target import Phase4Target


def _obs(
    rank: float,
    *,
    player: str = "P",
    class_name: str = "Mage",
    spec_name: str = "Frost",
    encounter_id: int = 3176,
) -> ExperimentalObservation:
    return ExperimentalObservation(
        report_code=f"R{player}",
        fight_id=1,
        player_name=player,
        observed_at_ms=1_000,
        target=Phase4Target(SpecId(class_name, spec_name), encounter_id, 5, 4),
        y_rank_percent=rank,
        features={},
    )


# -- regression metrics ---------------------------------------------------------


def test_perfect_prediction_scores_zero_error_and_unit_r2() -> None:
    metrics = regression_metrics([10.0, 50.0, 90.0], [10.0, 50.0, 90.0])
    assert metrics.mae == 0.0
    assert metrics.rmse == 0.0
    assert metrics.r2 == pytest.approx(1.0)
    assert metrics.spearman == pytest.approx(1.0)


def test_mae_and_rmse_are_computed_correctly() -> None:
    metrics = regression_metrics([0.0, 0.0], [3.0, 4.0])
    assert metrics.mae == pytest.approx(3.5)
    assert metrics.rmse == pytest.approx(3.5355339, abs=1e-6)


def test_rmse_punishes_a_large_error_more_than_mae() -> None:
    spread = regression_metrics([0.0, 0.0], [0.0, 10.0])
    even = regression_metrics([0.0, 0.0], [5.0, 5.0])
    assert spread.mae == even.mae
    assert spread.rmse > even.rmse


def test_predicting_the_mean_gives_r2_of_zero() -> None:
    metrics = regression_metrics([10.0, 20.0, 30.0], [20.0, 20.0, 20.0])
    assert metrics.r2 == pytest.approx(0.0)


def test_r2_is_none_when_truth_is_constant() -> None:
    """Undefined, not 0.0 or 1.0 — reporting a number here would be a lie."""
    assert regression_metrics([50.0, 50.0], [10.0, 90.0]).r2 is None


def test_empty_input_is_measurable_false() -> None:
    metrics = regression_metrics([], [])
    assert metrics.n == 0
    assert not metrics.is_measurable
    assert metrics.r2 is None


def test_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="equal-length"):
        regression_metrics([1.0], [1.0, 2.0])


# -- spearman -------------------------------------------------------------------


def test_spearman_is_one_for_a_monotonic_but_nonlinear_relation() -> None:
    """The reason Spearman matters: the product ranks recommendations."""
    assert spearman([1.0, 2.0, 3.0, 4.0], [1.0, 4.0, 9.0, 16.0]) == pytest.approx(1.0)


def test_spearman_is_minus_one_when_reversed() -> None:
    assert spearman([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]) == pytest.approx(-1.0)


def test_spearman_handles_ties_with_average_ranks() -> None:
    value = spearman([1.0, 1.0, 2.0, 2.0], [5.0, 5.0, 9.0, 9.0])
    assert value == pytest.approx(1.0)


def test_spearman_is_none_when_a_side_is_constant() -> None:
    assert spearman([1.0, 2.0, 3.0], [7.0, 7.0, 7.0]) is None


def test_spearman_is_none_below_two_points() -> None:
    assert spearman([1.0], [2.0]) is None


def test_spearman_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="equal-length"):
        spearman([1.0], [1.0, 2.0])


# -- baseline 0 -----------------------------------------------------------------


def test_median_baseline_predicts_the_training_median() -> None:
    baseline = MedianBaseline.fit([_obs(10.0), _obs(50.0), _obs(90.0)])
    assert baseline.value == 50.0
    assert baseline.predict([_obs(0.0), _obs(0.0)]) == [50.0, 50.0]


def test_median_baseline_averages_the_middle_of_an_even_sample() -> None:
    assert MedianBaseline.fit([_obs(10.0), _obs(20.0)]).value == 15.0


def test_median_baseline_refuses_an_empty_training_set() -> None:
    with pytest.raises(ValueError, match="empty training set"):
        MedianBaseline.fit([])


# -- split evaluation -------------------------------------------------------------


def test_evaluate_split_breaks_error_down_by_bucket_spec_and_encounter() -> None:
    observations = [
        _obs(10.0, player="A", spec_name="Frost", encounter_id=3176),
        _obs(90.0, player="B", spec_name="Fire", encounter_id=3177),
    ]
    evaluation = evaluate_split(observations, [12.0, 80.0])

    assert set(evaluation.by_bucket) == {"00-20", "80-100"}
    assert set(evaluation.by_spec) == {"Mage/Frost", "Mage/Fire"}
    assert set(evaluation.by_encounter) == {3176, 3177}
    assert evaluation.by_bucket["00-20"].mae == pytest.approx(2.0)
    assert evaluation.by_bucket["80-100"].mae == pytest.approx(10.0)


def test_evaluate_split_separates_seen_from_unseen_targets() -> None:
    """The measurement that decides H3/H4: a shared model's score on targets
    it never trained on.
    """
    seen = _obs(40.0, player="A", encounter_id=3176)
    unseen = _obs(60.0, player="B", encounter_id=3999)
    evaluation = evaluate_split(
        [seen, unseen], [42.0, 90.0], trained_target_ids=frozenset({seen.target.target_id})
    )

    assert evaluation.seen_targets is not None
    assert evaluation.unseen_targets is not None
    assert evaluation.seen_targets.mae == pytest.approx(2.0)
    assert evaluation.unseen_targets.mae == pytest.approx(30.0)


def test_evaluate_split_reports_none_for_an_absent_group() -> None:
    observation = _obs(40.0, player="A")
    evaluation = evaluate_split([observation], [40.0], trained_target_ids=frozenset())

    assert evaluation.seen_targets is None
    assert evaluation.unseen_targets is not None


def test_evaluate_split_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="equal-length"):
        evaluate_split([_obs(1.0)], [1.0, 2.0])


def test_evaluate_split_handles_no_observations() -> None:
    evaluation = evaluate_split([], [])
    assert not evaluation.overall.is_measurable
    assert evaluation.by_spec == {}


# -- architecture comparison --------------------------------------------------------


def test_comparison_reports_relative_degradation() -> None:
    comparison = ArchitectureComparison(
        reference_mae=10.0, candidate_mae=12.0, reference_spearman=0.5, candidate_spearman=0.45
    )
    assert comparison.mae_ratio == pytest.approx(1.2)
    assert comparison.spearman_ratio == pytest.approx(0.9)


def test_a_better_candidate_scores_below_one() -> None:
    comparison = ArchitectureComparison(
        reference_mae=10.0, candidate_mae=8.0, reference_spearman=0.5, candidate_spearman=0.6
    )
    assert comparison.mae_ratio is not None
    assert comparison.mae_ratio < 1.0
    assert comparison.spearman_ratio is not None
    assert comparison.spearman_ratio > 1.0


def test_comparison_ratios_are_none_when_the_reference_is_degenerate() -> None:
    comparison = ArchitectureComparison(
        reference_mae=0.0, candidate_mae=5.0, reference_spearman=None, candidate_spearman=0.4
    )
    assert comparison.mae_ratio is None
    assert comparison.spearman_ratio is None
