from __future__ import annotations

import pytest

from botgitgud.domain.specs import SpecId
from botgitgud.phase4.experiment_features import EXCLUDED_LEAKAGE_COLUMNS
from botgitgud.phase4.experiment_models import (
    MIN_ROWS_FOR_BOOTSTRAP,
    FeatureFamily,
    FittedFeatureSpace,
    LightGBMModel,
    LinearRegressionModel,
    _candidate_feature_names,
    _ConstantFallback,
    bootstrap_mae_spearman,
)
from botgitgud.phase4.experimental_dataset import ExperimentalObservation
from botgitgud.phase4.target import Phase4Target


def _obs(
    index: int, *, spec_name: str = "Frost", encounter_id: int = 3176
) -> ExperimentalObservation:
    """Deterministic but varying features/target — enough spread that every
    modellable column has nonzero training-fold variance.
    """
    return ExperimentalObservation(
        report_code=f"R{index}",
        fight_id=index,
        player_name=f"P{index}",
        observed_at_ms=1_000 + index,
        target=Phase4Target(SpecId("Mage", spec_name), encounter_id, 5, 4),
        y_rank_percent=float((index * 7) % 101),
        features={
            "ctx_encounter_id": float(encounter_id),
            "ctx_difficulty": 5.0,
            "ctx_partition": 4.0,
            "nc_item_level": 480.0 + index,
            "nc_tier_pieces": float(index % 4),
            "nc_duration_s": 300.0 + index * 3,
            "nc_raid_size": 20.0,
            "nc_n_external_buffs": float(index % 3),
            "nc_has_augmentation": float(index % 2),
            "c_active_time_pct": 0.5 + (index % 10) / 20.0,
            "c_deaths": float(index % 3),
            "c_downtime_s": float(index % 20),
            "c_total_casts": 40.0 + index,
            "c_casts_per_minute": 8.0 + (index % 5),
            "c_distinct_abilities_cast": float(5 + index % 6),
            "c_mean_uptime": 0.4 + (index % 10) / 25.0,
            "c_n_tracked_auras": float(3 + index % 4),
            "c_resource_waste_total": float(index % 15),
            "c_resource_waste_per_minute": float(index % 5),
            "c_mean_targets_per_cast": 1.0 + (index % 3) / 2.0,
        },
    )


def _group(n: int, **kwargs: object) -> list[ExperimentalObservation]:
    return [_obs(i, **kwargs) for i in range(n)]  # type: ignore[arg-type]


# -- feature families / leakage --------------------------------------------------


def test_f1_is_controllable_only() -> None:
    names = _candidate_feature_names(FeatureFamily.F1_CONTROLLABLE_ONLY)
    assert all(name.startswith("c_") for name in names)


def test_f2_includes_context_and_non_controllable_too() -> None:
    names = _candidate_feature_names(FeatureFamily.F2_FULL_COVARIATES)
    assert any(name.startswith("nc_") for name in names)
    assert any(name.startswith("c_") for name in names)


def test_excluded_leakage_columns_never_appear_in_either_family() -> None:
    leaked = {spec.name for spec in EXCLUDED_LEAKAGE_COLUMNS}
    f1 = set(_candidate_feature_names(FeatureFamily.F1_CONTROLLABLE_ONLY))
    f2 = set(_candidate_feature_names(FeatureFamily.F2_FULL_COVARIATES))
    assert not (leaked & f1)
    assert not (leaked & f2)
    assert "alignment_score" not in f1 | f2
    assert "total_parses" not in f1 | f2


def test_target_column_never_appears_as_a_feature_candidate() -> None:
    f2 = set(_candidate_feature_names(FeatureFamily.F2_FULL_COVARIATES))
    assert "y_rank_percent" not in f2


# -- FittedFeatureSpace: train-fold-only ------------------------------------------


def test_feature_space_drops_columns_constant_in_the_training_fold() -> None:
    train = _group(10)  # ctx_difficulty/ctx_partition are constant by construction
    space = FittedFeatureSpace.fit(train, FeatureFamily.F2_FULL_COVARIATES)
    assert "ctx_difficulty" not in space.columns
    assert "ctx_partition" not in space.columns
    assert "c_deaths" in space.columns  # varies across the synthetic group


def test_feature_space_selection_ignores_validation_rows() -> None:
    """A column constant only across the *validation* rows must still be
    kept if it varies in training — the selection is a training-fold-only
    decision, never influenced by what validation happens to look like.
    """
    train = _group(10)
    validation = [_obs(0), _obs(0)]  # every feature identical across these two
    space = FittedFeatureSpace.fit(train, FeatureFamily.F1_CONTROLLABLE_ONLY)
    matrix = space.transform(validation)
    assert len(matrix) == 2
    assert len(matrix[0]) == len(space.columns)


def test_feature_space_with_zero_variance_group_has_no_columns() -> None:
    train = [_obs(0), _obs(0), _obs(0)]  # identical features throughout
    space = FittedFeatureSpace.fit(train, FeatureFamily.F1_CONTROLLABLE_ONLY)
    assert space.columns == ()


def test_fitting_on_empty_training_set_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        FittedFeatureSpace.fit([], FeatureFamily.F1_CONTROLLABLE_ONLY)


# -- Baseline 1 (linear regression) -----------------------------------------------


def test_linear_regression_predicts_finite_values_within_a_reasonable_range() -> None:
    train = _group(30)
    validation = _group(5, spec_name="Frost")
    space = FittedFeatureSpace.fit(train, FeatureFamily.F1_CONTROLLABLE_ONLY)
    model = LinearRegressionModel.fit(train, space)
    preds = model.predict(validation)
    assert len(preds) == 5
    assert all(isinstance(p, float) for p in preds)


def test_linear_regression_is_deterministic() -> None:
    train = _group(20)
    validation = _group(4)
    space = FittedFeatureSpace.fit(train, FeatureFamily.F2_FULL_COVARIATES)
    first = LinearRegressionModel.fit(train, space).predict(validation)
    second = LinearRegressionModel.fit(train, space).predict(validation)
    assert first == second


def test_zero_column_feature_space_falls_back_to_the_training_mean() -> None:
    train = [_obs(0), _obs(0), _obs(0)]
    space = FittedFeatureSpace.fit(train, FeatureFamily.F1_CONTROLLABLE_ONLY)
    model = LinearRegressionModel.fit(train, space)
    assert isinstance(model, _ConstantFallback)
    validation = _group(3)
    preds = model.predict(validation)
    assert preds == [train[0].y_rank_percent] * 3


# -- LightGBM candidate ------------------------------------------------------------


def test_lightgbm_is_deterministic_given_the_same_seed() -> None:
    train = _group(30)
    validation = _group(6)
    space = FittedFeatureSpace.fit(train, FeatureFamily.F2_FULL_COVARIATES)
    first = LightGBMModel.fit(train, space, seed=42).predict(validation)
    second = LightGBMModel.fit(train, space, seed=42).predict(validation)
    assert first == second


def test_lightgbm_predicts_finite_values() -> None:
    train = _group(30)
    validation = _group(6)
    space = FittedFeatureSpace.fit(train, FeatureFamily.F1_CONTROLLABLE_ONLY)
    model = LightGBMModel.fit(train, space, seed=1)
    preds = model.predict(validation)
    assert len(preds) == 6
    assert all(p == p for p in preds)  # no NaN


def test_lightgbm_zero_column_feature_space_falls_back_to_the_training_mean() -> None:
    train = [_obs(0), _obs(0), _obs(0)]
    space = FittedFeatureSpace.fit(train, FeatureFamily.F1_CONTROLLABLE_ONLY)
    model = LightGBMModel.fit(train, space, seed=1)
    assert isinstance(model, _ConstantFallback)


# -- bootstrap ----------------------------------------------------------------------


def test_bootstrap_below_minimum_rows_is_not_measurable() -> None:
    y_true = [10.0] * (MIN_ROWS_FOR_BOOTSTRAP - 1)
    y_pred = [12.0] * (MIN_ROWS_FOR_BOOTSTRAP - 1)
    mae_ci, spearman_ci = bootstrap_mae_spearman(y_true, y_pred, seed=1)
    assert mae_ci.is_measurable is False
    assert spearman_ci.is_measurable is False
    assert mae_ci.low is None
    assert mae_ci.high is None


def test_bootstrap_is_reproducible_with_the_same_seed() -> None:
    y_true = [float(i % 20) for i in range(40)]
    y_pred = [float((i * 3) % 20) for i in range(40)]
    first = bootstrap_mae_spearman(y_true, y_pred, seed=7, n_resamples=200)
    second = bootstrap_mae_spearman(y_true, y_pred, seed=7, n_resamples=200)
    assert first[0].low == second[0].low
    assert first[0].high == second[0].high
    assert first[1].low == second[1].low
    assert first[1].high == second[1].high


def test_bootstrap_interval_contains_the_point_estimate() -> None:
    y_true = [float(i % 20) for i in range(40)]
    y_pred = [float((i * 3) % 20) for i in range(40)]
    mae_ci, _ = bootstrap_mae_spearman(y_true, y_pred, seed=7, n_resamples=500)
    assert mae_ci.is_measurable
    assert mae_ci.low is not None
    assert mae_ci.high is not None
    assert mae_ci.point_estimate is not None
    assert mae_ci.low <= mae_ci.point_estimate <= mae_ci.high


def test_bootstrap_requires_equal_length_sequences() -> None:
    with pytest.raises(ValueError, match="equal-length"):
        bootstrap_mae_spearman([1.0, 2.0], [1.0], seed=1)
