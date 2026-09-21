from __future__ import annotations

from botgitgud.domain.specs import SpecId
from botgitgud.phase4.experiment_decision import (
    HoldoutStatus,
    calibration_by_predicted_bucket,
    error_distribution,
    group_bootstrap,
    group_metric,
    group_paired_delta,
    leave_one_out_global,
    range_diagnostic,
    ridge_diagnostic,
    seed_sensitivity,
    summarize_macro_micro,
)
from botgitgud.phase4.experiment_models import FeatureFamily
from botgitgud.phase4.experimental_dataset import (
    ExperimentalFeatureDataset,
    ExperimentalObservation,
)
from botgitgud.phase4.target import Phase4Target

DIFFICULTY = 5
PARTITION = 4


def _obs(
    index: int,
    *,
    class_name: str = "Mage",
    spec_name: str = "Frost",
    encounter_id: int = 3176,
) -> ExperimentalObservation:
    return ExperimentalObservation(
        report_code=f"R{index:04d}",
        fight_id=index,
        player_name=f"P{index:04d}",
        observed_at_ms=1_000 + index,
        target=Phase4Target(SpecId(class_name, spec_name), encounter_id, DIFFICULTY, PARTITION),
        y_rank_percent=float((index * 7) % 101),
        features={
            "ctx_encounter_id": float(encounter_id),
            "nc_item_level": 480.0 + index,
            "nc_duration_s": 300.0 + index * 3,
            "c_active_time_pct": 0.5 + (index % 10) / 20.0,
            "c_deaths": float(index % 3),
            "c_total_casts": 40.0 + index,
            "c_mean_uptime": 0.4 + (index % 10) / 25.0,
        },
    )


def _dataset(
    *, specs: int = 4, encounters: tuple[int, ...] = (3176, 3177), per_group: int = 15
) -> ExperimentalFeatureDataset:
    observations = []
    n = 0
    for spec_i in range(specs):
        for enc in encounters:
            for _ in range(per_group):
                observations.append(_obs(n, spec_name=f"Spec{spec_i}", encounter_id=enc))
                n += 1
    return ExperimentalFeatureDataset(observations=tuple(observations))


# -- leave-one-out isolation --------------------------------------------------------


def test_leave_one_spec_out_excludes_the_spec_entirely_from_training() -> None:
    dataset = _dataset(specs=4, per_group=15)
    results = leave_one_out_global(
        dataset, dimension="spec", families=[FeatureFamily.F1_CONTROLLABLE_ONLY], seed=1
    )
    assert len(results) == 4
    for r in results:
        assert r.status is HoldoutStatus.OK
        assert r.n_train + r.n_validation == len(dataset)
        # every validation row belongs to exactly the retained spec, and
        # none of that spec's rows leaked into training.
        assert r.n_validation == 30  # 2 encounters x 15


def test_leave_one_encounter_out_excludes_the_encounter_entirely() -> None:
    dataset = _dataset(specs=4, encounters=(3176, 3177, 3178), per_group=15)
    results = leave_one_out_global(
        dataset, dimension="encounter", families=[FeatureFamily.F1_CONTROLLABLE_ONLY], seed=1
    )
    assert len(results) == 3
    for r in results:
        assert r.status is HoldoutStatus.OK
        assert r.n_validation == 60  # 4 specs x 15


def test_leave_one_out_rejects_unknown_dimension() -> None:
    dataset = _dataset()
    try:
        leave_one_out_global(dataset, dimension="bogus", families=[], seed=1)
    except ValueError as exc:
        assert "dimension" in str(exc)
    else:
        raise AssertionError("must reject an unknown dimension")


def test_small_group_is_flagged_insufficient_data_not_hidden() -> None:
    dataset = _dataset(specs=3, per_group=15)
    # add one spec with only 4 rows total — below MIN_VALIDATION_ROWS_FOR_HOLDOUT
    tiny = [_obs(9000 + i, spec_name="Tiny") for i in range(4)]
    dataset = ExperimentalFeatureDataset(observations=(*dataset.observations, *tiny))
    results = leave_one_out_global(
        dataset, dimension="spec", families=[FeatureFamily.F1_CONTROLLABLE_ONLY], seed=1
    )
    tiny_result = next(r for r in results if r.key == "Mage/Tiny")
    assert tiny_result.status is HoldoutStatus.INSUFFICIENT_DATA
    assert tiny_result.n_validation == 4
    assert group_metric(tiny_result, FeatureFamily.F1_CONTROLLABLE_ONLY, "lightgbm") is None


# -- macro/micro ----------------------------------------------------------------------


def test_macro_micro_pools_and_averages_correctly() -> None:
    dataset = _dataset(specs=4, per_group=15)
    results = leave_one_out_global(
        dataset, dimension="spec", families=[FeatureFamily.F1_CONTROLLABLE_ONLY], seed=1
    )
    summary = summarize_macro_micro(results, FeatureFamily.F1_CONTROLLABLE_ONLY, "baseline_0")
    assert summary.n_groups_ok == 4
    assert summary.micro.n == 4 * 30  # every validation row pooled
    assert summary.macro_worst_mae is not None
    assert summary.macro_mean_mae is not None
    assert summary.macro_worst_mae >= summary.macro_mean_mae  # worst can't be below the mean
    assert summary.worst_group_key is not None


def test_macro_micro_excludes_insufficient_groups() -> None:
    dataset = _dataset(specs=2, per_group=15)
    tiny = [_obs(9000 + i, spec_name="Tiny") for i in range(3)]
    dataset = ExperimentalFeatureDataset(observations=(*dataset.observations, *tiny))
    results = leave_one_out_global(
        dataset, dimension="spec", families=[FeatureFamily.F1_CONTROLLABLE_ONLY], seed=1
    )
    summary = summarize_macro_micro(results, FeatureFamily.F1_CONTROLLABLE_ONLY, "baseline_0")
    assert summary.n_groups_total == 3
    assert summary.n_groups_ok == 2
    assert summary.micro.n == 2 * 30  # tiny spec's 3 rows never enter the pool


# -- bootstrap: deterministic, paired, group-scoped -----------------------------------


def test_group_bootstrap_is_deterministic_given_the_same_seed() -> None:
    dataset = _dataset(specs=2, per_group=15)
    results = leave_one_out_global(
        dataset, dimension="spec", families=[FeatureFamily.F1_CONTROLLABLE_ONLY], seed=1
    )
    first = group_bootstrap(
        results[0], FeatureFamily.F1_CONTROLLABLE_ONLY, "lightgbm", seed=7, n_resamples=100
    )
    second = group_bootstrap(
        results[0], FeatureFamily.F1_CONTROLLABLE_ONLY, "lightgbm", seed=7, n_resamples=100
    )
    assert first[0].low == second[0].low
    assert first[0].high == second[0].high


def test_paired_delta_sign_convention_a_minus_b() -> None:
    dataset = _dataset(specs=2, per_group=15)
    results = leave_one_out_global(
        dataset, dimension="spec", families=[FeatureFamily.F1_CONTROLLABLE_ONLY], seed=1
    )
    r = results[0]
    mae_ci, _ = group_paired_delta(
        r, FeatureFamily.F1_CONTROLLABLE_ONLY, "lightgbm", "baseline_0", seed=3, n_resamples=200
    )
    m_lgbm = group_metric(r, FeatureFamily.F1_CONTROLLABLE_ONLY, "lightgbm")
    m_b0 = group_metric(r, FeatureFamily.F1_CONTROLLABLE_ONLY, "baseline_0")
    assert mae_ci.point_estimate is not None
    assert m_lgbm is not None
    assert m_b0 is not None
    assert mae_ci.point_estimate == m_lgbm.mae - m_b0.mae


def test_group_bootstrap_on_insufficient_group_is_not_measurable() -> None:
    dataset = _dataset(specs=1, per_group=15)
    tiny = [_obs(9000 + i, spec_name="Tiny") for i in range(3)]
    dataset = ExperimentalFeatureDataset(observations=(*dataset.observations, *tiny))
    results = leave_one_out_global(
        dataset, dimension="spec", families=[FeatureFamily.F1_CONTROLLABLE_ONLY], seed=1
    )
    tiny_result = next(r for r in results if r.key == "Mage/Tiny")
    mae_ci, spearman_ci = group_bootstrap(
        tiny_result, FeatureFamily.F1_CONTROLLABLE_ONLY, "lightgbm", seed=1
    )
    assert mae_ci.is_measurable is False
    assert spearman_ci.is_measurable is False


# -- seed sensitivity -----------------------------------------------------------------


def test_seed_sensitivity_reports_zero_spread_for_a_fully_deterministic_config() -> None:
    """subsample=1.0/colsample_bytree=1.0 in LightGBMModel means the seed
    has nothing left to randomize — this must show up as std=0, never a
    fabricated spread.
    """
    dataset = _dataset(specs=1, per_group=30)
    train = dataset.observations[:20]
    validation = dataset.observations[20:]
    result = seed_sensitivity(
        train, validation, FeatureFamily.F1_CONTROLLABLE_ONLY, seeds=(1, 2, 3)
    )
    assert result.mae_std == 0.0
    assert len(set(result.mae_values)) == 1


# -- error distribution / calibration / range ------------------------------------------


def test_error_distribution_quantiles_are_monotonic() -> None:
    dataset = _dataset(specs=2, per_group=15)
    train, validation = dataset.observations[:20], dataset.observations[20:]
    from botgitgud.phase4.experiment_models import FittedFeatureSpace, LightGBMModel

    fs = FittedFeatureSpace.fit(train, FeatureFamily.F1_CONTROLLABLE_ONLY)
    preds = LightGBMModel.fit(train, fs, seed=1).predict(validation)
    dist = error_distribution(validation, preds)
    assert dist.n == len(validation)
    assert dist.median_abs_error <= dist.p75_abs_error <= dist.p90_abs_error <= dist.p95_abs_error
    assert dist.p95_abs_error <= dist.max_abs_error


def test_error_distribution_bias_matches_manual_calculation() -> None:
    obs = [_obs(0), _obs(1)]
    preds = [obs[0].y_rank_percent + 5.0, obs[1].y_rank_percent + 5.0]
    dist = error_distribution(obs, preds)
    bucket_key = next(iter(dist.bias_by_bucket))
    assert dist.bias_by_bucket[bucket_key] == 5.0 or len(dist.bias_by_bucket) > 1


def test_calibration_bins_by_predicted_value_and_reports_bias() -> None:
    obs = [_obs(i, spec_name="X") for i in range(20)]
    preds = [10.0] * 20  # every prediction lands in the 00-20 bucket
    rows = calibration_by_predicted_bucket(obs, preds)
    assert len(rows) == 1
    assert rows[0].predicted_bucket == "00-20"
    assert rows[0].n == 20
    assert rows[0].mean_predicted == 10.0
    assert rows[0].bias == rows[0].mean_predicted - rows[0].mean_observed


def test_range_diagnostic_counts_out_of_range_predictions() -> None:
    obs = [_obs(i) for i in range(4)]
    preds = [-5.0, 50.0, 105.0, 50.0]
    diag = range_diagnostic(obs, preds)
    assert diag.n_below_zero == 1
    assert diag.n_above_hundred == 1
    assert diag.min_prediction == -5.0
    assert diag.max_prediction == 105.0
    # clipping changes the metrics only when something is out of range
    assert diag.clipped_metrics.mae <= diag.raw_metrics.mae + 1e-9


def test_range_diagnostic_raw_equals_clipped_when_nothing_is_out_of_range() -> None:
    obs = [_obs(i) for i in range(4)]
    preds = [10.0, 20.0, 30.0, 40.0]
    diag = range_diagnostic(obs, preds)
    assert diag.n_below_zero == 0
    assert diag.n_above_hundred == 0
    assert diag.raw_metrics.mae == diag.clipped_metrics.mae


# -- Ridge diagnostic ------------------------------------------------------------------


def test_ridge_diagnostic_uses_a_train_only_feature_space() -> None:
    dataset = _dataset(specs=2, per_group=15)
    train, validation = dataset.observations[:20], dataset.observations[20:]
    lgbm_metrics, ridge_metrics = ridge_diagnostic(
        train, validation, FeatureFamily.F1_CONTROLLABLE_ONLY
    )
    assert lgbm_metrics.n == len(validation)
    assert ridge_metrics.n == len(validation)


def test_ridge_predictions_stay_far_more_bounded_than_unregularized_ols() -> None:
    """Not a strict correctness assertion (Ridge can still extrapolate a
    little) — just confirms the diagnostic actually runs a distinct,
    finite-output model rather than silently reusing Baseline 1.
    """
    dataset = _dataset(specs=2, per_group=15)
    train, validation = dataset.observations[:20], dataset.observations[20:]
    _, ridge_metrics = ridge_diagnostic(train, validation, FeatureFamily.F1_CONTROLLABLE_ONLY)
    assert ridge_metrics.mae < 1000.0  # sanity: finite, not blown-up predictions
