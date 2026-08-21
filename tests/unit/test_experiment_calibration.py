from __future__ import annotations

from itertools import pairwise

import pytest

from botgitgud.domain.specs import SpecId
from botgitgud.phase4.experiment_calibration import (
    MIN_CALIBRATION_ROWS_FOR_ISOTONIC,
    CalibrationMethod,
    CalibrationStatus,
    ConfidenceLevel,
    IsotonicCalibrator,
    LinearCalibrator,
    bias_by_group,
    calibration_by_observed_bucket,
    confidence_level,
    cross_fitted_raw_diagnostic,
    fit_and_apply_calibrators,
    mean_signed_error,
    temporal_train_calibration_validation_split,
)
from botgitgud.phase4.experiment_splits import SplitInvariantError
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
    at_ms: int | None = None,
    rank: float | None = None,
) -> ExperimentalObservation:
    return ExperimentalObservation(
        report_code=f"R{index:04d}",
        fight_id=index,
        player_name=f"P{index:04d}",
        observed_at_ms=at_ms if at_ms is not None else 1_000 + index,
        target=Phase4Target(SpecId(class_name, spec_name), encounter_id, DIFFICULTY, PARTITION),
        y_rank_percent=rank if rank is not None else float((index * 7) % 101),
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


def _wide_dataset(
    n: int = 150, *, specs: int = 3, encounters: tuple[int, ...] = (3176, 3177)
) -> ExperimentalFeatureDataset:
    observations = []
    i = 0
    while len(observations) < n:
        spec = f"Spec{i % specs}"
        enc = encounters[i % len(encounters)]
        observations.append(_obs(i, spec_name=spec, encounter_id=enc, at_ms=1_000 + i))
        i += 1
    return ExperimentalFeatureDataset(observations=tuple(observations))


# -- three-way temporal split ---------------------------------------------------------


def test_three_way_split_is_strictly_temporally_ordered_and_disjoint() -> None:
    dataset = _wide_dataset(150)
    split = temporal_train_calibration_validation_split(dataset)
    assert split.is_usable
    train_keys = {o.observation_key for o in split.train}
    cal_keys = {o.observation_key for o in split.calibration}
    val_keys = {o.observation_key for o in split.validation}
    assert not (train_keys & cal_keys)
    assert not (train_keys & val_keys)
    assert not (cal_keys & val_keys)
    assert max(o.observed_at_ms for o in split.train) <= min(
        o.observed_at_ms for o in split.calibration
    )
    assert max(o.observed_at_ms for o in split.calibration) <= min(
        o.observed_at_ms for o in split.validation
    )


def test_three_way_split_covers_every_row_exactly_once() -> None:
    dataset = _wide_dataset(150)
    split = temporal_train_calibration_validation_split(dataset)
    assert len(split.train) + len(split.calibration) + len(split.validation) == len(dataset)


def test_three_way_split_records_exact_periods() -> None:
    dataset = _wide_dataset(150)
    split = temporal_train_calibration_validation_split(dataset)
    assert split.train_period_ms is not None
    assert split.calibration_period_ms is not None
    assert split.validation_period_ms is not None
    assert split.train_period_ms[1] <= split.calibration_period_ms[0]
    assert split.calibration_period_ms[1] <= split.validation_period_ms[0]


def test_three_way_split_rejects_invalid_fractions() -> None:
    dataset = _wide_dataset(50)
    with pytest.raises(ValueError, match="0, 1"):
        temporal_train_calibration_validation_split(dataset, calibration_fraction=0.0)
    with pytest.raises(ValueError, match="room for training"):
        temporal_train_calibration_validation_split(
            dataset, calibration_fraction=0.6, validation_fraction=0.6
        )


# -- calibrator never sees validation labels -------------------------------------------


def test_validation_labels_never_influence_model_or_calibrator() -> None:
    """Black-box leakage check: perturbing ONLY the validation fold's labels
    (same rows, same features, different y) must not change a single raw
    prediction or a single calibrated prediction — both are produced by a
    model/calibrator fit exclusively on train/calibration.
    """
    dataset = _wide_dataset(150)
    split = temporal_train_calibration_validation_split(dataset)
    run_a = fit_and_apply_calibrators(split, seed=1)

    perturbed_validation = tuple(
        ExperimentalObservation(
            report_code=o.report_code,
            fight_id=o.fight_id,
            player_name=o.player_name,
            observed_at_ms=o.observed_at_ms,
            target=o.target,
            y_rank_percent=100.0 - o.y_rank_percent,
            features=o.features,
        )
        for o in split.validation
    )
    perturbed_split = split.__class__(
        train=split.train,
        calibration=split.calibration,
        validation=perturbed_validation,
        train_period_ms=split.train_period_ms,
        calibration_period_ms=split.calibration_period_ms,
        validation_period_ms=split.validation_period_ms,
    )
    run_b = fit_and_apply_calibrators(perturbed_split, seed=1)

    assert run_a.raw_validation_predictions == run_b.raw_validation_predictions
    assert (
        run_a.results[CalibrationMethod.LINEAR].predictions
        == run_b.results[CalibrationMethod.LINEAR].predictions
    )


def test_raw_prediction_is_never_overwritten_by_calibration() -> None:
    dataset = _wide_dataset(150)
    split = temporal_train_calibration_validation_split(dataset)
    run = fit_and_apply_calibrators(split, seed=1)
    raw = run.results[CalibrationMethod.NONE].predictions
    linear = run.results[CalibrationMethod.LINEAR].predictions
    assert raw == run.raw_validation_predictions
    assert raw != linear or all(a == b for a, b in zip(raw, linear, strict=True))


def test_calibrated_predictions_are_persisted_alongside_raw() -> None:
    dataset = _wide_dataset(150)
    split = temporal_train_calibration_validation_split(dataset)
    run = fit_and_apply_calibrators(split, seed=1)
    for method in (CalibrationMethod.NONE, CalibrationMethod.LINEAR, CalibrationMethod.ISOTONIC):
        result = run.results[method]
        if result.status is CalibrationStatus.EVALUATED:
            assert len(result.predictions) == len(split.validation)


def test_fit_and_apply_calibrators_rejects_an_unusable_split() -> None:
    dataset = _wide_dataset(150)
    split = temporal_train_calibration_validation_split(dataset)
    empty_split = split.__class__(
        train=split.train,
        calibration=(),
        validation=split.validation,
        train_period_ms=split.train_period_ms,
        calibration_period_ms=None,
        validation_period_ms=split.validation_period_ms,
    )
    with pytest.raises(ValueError, match="unusable split"):
        fit_and_apply_calibrators(empty_split, seed=1)


# -- linear calibration --------------------------------------------------------------


def test_linear_calibration_recovers_a_known_affine_relationship() -> None:
    raw = [10.0, 20.0, 30.0, 40.0, 50.0]
    observed = [2 * r + 1.0 for r in raw]  # observed = 2*raw + 1, noiseless
    calibrator = LinearCalibrator.fit(raw, observed)
    predicted = calibrator.predict([15.0, 25.0])
    assert predicted[0] == pytest.approx(2 * 15.0 + 1.0, abs=1e-6)
    assert predicted[1] == pytest.approx(2 * 25.0 + 1.0, abs=1e-6)


def test_linear_calibration_preserves_spearman_under_positive_slope() -> None:
    """A strictly increasing affine transform cannot change rank order —
    Spearman before/after must match exactly. `raw`/`observed` are both
    clearly increasing (with noise), so the fitted OLS slope is guaranteed
    positive.
    """
    from botgitgud.phase4.experiment_metrics import spearman

    raw = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    observed = [2.0, 1.0, 5.0, 4.0, 7.0, 6.0, 9.0, 10.0]
    calibrator = LinearCalibrator.fit(raw, observed)
    calibrated = calibrator.predict(raw)

    rho_raw = spearman(observed, raw)
    rho_calibrated = spearman(observed, calibrated)
    assert rho_raw is not None
    assert rho_calibrated is not None
    assert rho_calibrated == pytest.approx(rho_raw, abs=1e-9)


# -- isotonic calibration --------------------------------------------------------------


def test_isotonic_calibration_is_monotonic_non_decreasing() -> None:
    raw = [float(i) for i in range(40)]
    observed = [float((i * 7) % 101) for i in range(40)]
    calibrator = IsotonicCalibrator.fit(raw, observed)
    predicted = calibrator.predict(sorted(raw))
    assert all(a <= b + 1e-9 for a, b in pairwise(predicted))


def test_isotonic_not_evaluable_below_the_sample_floor() -> None:
    dataset = _wide_dataset(50)
    # force a tiny calibration fold via extreme fractions
    split = temporal_train_calibration_validation_split(
        dataset, calibration_fraction=0.05, validation_fraction=0.15
    )
    assert len(split.calibration) < MIN_CALIBRATION_ROWS_FOR_ISOTONIC
    run = fit_and_apply_calibrators(split, seed=1)
    result = run.results[CalibrationMethod.ISOTONIC]
    assert result.status is CalibrationStatus.NOT_EVALUABLE
    assert result.reason is not None
    assert result.predictions == ()


def test_isotonic_evaluable_above_the_sample_floor() -> None:
    dataset = _wide_dataset(300)
    split = temporal_train_calibration_validation_split(dataset)
    assert len(split.calibration) >= MIN_CALIBRATION_ROWS_FOR_ISOTONIC
    run = fit_and_apply_calibrators(split, seed=1)
    result = run.results[CalibrationMethod.ISOTONIC]
    assert result.status is CalibrationStatus.EVALUATED
    assert len(result.predictions) == len(split.validation)


# -- calibration buckets / bias by group -------------------------------------------------


def test_calibration_by_observed_bucket_never_hides_n() -> None:
    observations = [_obs(i, rank=10.0) for i in range(5)] + [
        _obs(100 + i, rank=90.0) for i in range(2)
    ]
    raw = [12.0] * 5 + [85.0] * 2
    calibrated = [11.0] * 5 + [88.0] * 2
    rows = calibration_by_observed_bucket(observations, raw, calibrated)
    by_bucket = {r.bucket: r for r in rows}
    assert by_bucket["00-20"].n == 5
    assert by_bucket["80-100"].n == 2


def test_mean_signed_error_sign_convention() -> None:
    assert mean_signed_error([10.0, 10.0], [12.0, 14.0]) == pytest.approx(
        3.0
    )  # overshoot: positive
    assert mean_signed_error([10.0, 10.0], [8.0, 6.0]) == pytest.approx(
        -3.0
    )  # undershoot: negative


def test_bias_by_group_reports_both_raw_and_calibrated() -> None:
    observations = [_obs(i, spec_name="A") for i in range(5)] + [
        _obs(10 + i, spec_name="B") for i in range(3)
    ]
    raw = [o.y_rank_percent + 5.0 for o in observations]
    calibrated = [o.y_rank_percent + 1.0 for o in observations]
    rows = bias_by_group(observations, raw, calibrated, lambda o: o.spec_key)
    by_key = {r.key: r for r in rows}
    assert by_key["Mage/A"].n == 5
    assert by_key["Mage/A"].raw_bias == pytest.approx(5.0)
    assert by_key["Mage/A"].calibrated_bias == pytest.approx(1.0)
    assert by_key["Mage/B"].n == 3


# -- cross-fitted diagnostic (offline only) -----------------------------------------------


def test_cross_fitted_diagnostic_covers_the_whole_dataset() -> None:
    dataset = _wide_dataset(150)
    result = cross_fitted_raw_diagnostic(dataset, seed=1, k=5)
    assert result.n == len(dataset)


def test_cross_fitted_diagnostic_is_deterministic() -> None:
    dataset = _wide_dataset(150)
    first = cross_fitted_raw_diagnostic(dataset, seed=1, k=5)
    second = cross_fitted_raw_diagnostic(dataset, seed=1, k=5)
    assert first.mae == second.mae
    assert first.spearman == second.spearman


# -- confidence framework --------------------------------------------------------------


def test_confidence_high_only_for_interior_and_well_covered() -> None:
    level = confidence_level(predicted_bucket="40-60", spec_coverage=40, encounter_coverage=60)
    assert level is ConfidenceLevel.HIGH


def test_confidence_never_high_for_extreme_bucket() -> None:
    level = confidence_level(predicted_bucket="80-100", spec_coverage=1000, encounter_coverage=1000)
    assert level is not ConfidenceLevel.HIGH


def test_confidence_low_for_poor_coverage() -> None:
    level = confidence_level(predicted_bucket="40-60", spec_coverage=2, encounter_coverage=3)
    assert level is ConfidenceLevel.LOW


def test_confidence_medium_for_moderate_coverage() -> None:
    level = confidence_level(predicted_bucket="40-60", spec_coverage=20, encounter_coverage=5)
    assert level is ConfidenceLevel.MEDIUM


# -- determinism end-to-end -----------------------------------------------------------


def test_fit_and_apply_calibrators_is_deterministic() -> None:
    dataset = _wide_dataset(300)
    split = temporal_train_calibration_validation_split(dataset)
    first = fit_and_apply_calibrators(split, seed=7)
    second = fit_and_apply_calibrators(split, seed=7)
    assert first.raw_validation_predictions == second.raw_validation_predictions
    assert (
        first.results[CalibrationMethod.LINEAR].predictions
        == second.results[CalibrationMethod.LINEAR].predictions
    )


def test_split_invariant_error_is_the_project_wide_type() -> None:
    assert issubclass(SplitInvariantError, AssertionError)
