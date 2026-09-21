"""Phase 4 — Global Model Validation & Calibration Gate
(docs/fase4-global-model-validation.md).

Freezes the already-decided candidate (`MODEL_GLOBAL`, F2, LightGBM —
docs/fase4-architecture-decision.md §17-18) and asks a narrower question:
are its raw predictions well-calibrated, and can a simple, auditable
calibrator fix that without leaking validation labels into the fit?

Three-way temporal split (train -> calibration -> validation), strictly
ordered in time — mirrors `experiment_splits.py`'s own tie-avoidance cut
logic (`_temporal_cut`) but applied twice. Calibrators
(`LinearCalibrator`, `IsotonicCalibrator`) are fit ONLY on the
calibration fold's (raw_prediction, observed) pairs; validation labels
never reach a calibrator's `.fit()`. RAW and CALIBRATED predictions are
always kept side by side — nothing here silently overwrites raw output.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from botgitgud.phase4.experiment import percentile_bucket
from botgitgud.phase4.experiment_metrics import RegressionMetrics, regression_metrics
from botgitgud.phase4.experiment_models import FeatureFamily, FittedFeatureSpace, LightGBMModel
from botgitgud.phase4.experiment_splits import SplitInvariantError
from botgitgud.phase4.experimental_dataset import (
    ExperimentalFeatureDataset,
    ExperimentalObservation,
)

DEFAULT_CALIBRATION_FRACTION = 0.15
DEFAULT_VALIDATION_FRACTION = 0.15
# Below this many (raw_prediction, observed) pairs, a monotonic isotonic
# fit is more overfitting than calibration — reported as not_evaluable,
# never silently attempted. Chosen well above the degrees of freedom an
# isotonic step function needs to be meaningful, not tuned against any
# validation result.
MIN_CALIBRATION_ROWS_FOR_ISOTONIC = 30
CROSS_FIT_K = 5


# -- three-way temporal split --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TemporalThreeWaySplit:
    train: tuple[ExperimentalObservation, ...]
    calibration: tuple[ExperimentalObservation, ...]
    validation: tuple[ExperimentalObservation, ...]
    train_period_ms: tuple[int, int] | None
    calibration_period_ms: tuple[int, int] | None
    validation_period_ms: tuple[int, int] | None

    @property
    def is_usable(self) -> bool:
        return bool(self.train) and bool(self.calibration) and bool(self.validation)


def _cut_before(ordered: Sequence[ExperimentalObservation], index: int) -> int:
    """Pushes `index` back over any run of rows sharing the boundary
    timestamp — the same tie-avoidance `experiment_splits._temporal_cut`
    uses, so a single instant never straddles a split.
    """
    if index <= 0 or index >= len(ordered):
        return index
    boundary = ordered[index].observed_at_ms
    while index > 0 and ordered[index - 1].observed_at_ms == boundary:
        index -= 1
    return index


def _span(observations: Sequence[ExperimentalObservation]) -> tuple[int, int] | None:
    if not observations:
        return None
    times = [o.observed_at_ms for o in observations]
    return (min(times), max(times))


def _verify_three_way(split: TemporalThreeWaySplit) -> None:
    train_keys = {o.observation_key for o in split.train}
    cal_keys = {o.observation_key for o in split.calibration}
    val_keys = {o.observation_key for o in split.validation}
    if train_keys & cal_keys or train_keys & val_keys or cal_keys & val_keys:
        raise SplitInvariantError("train/calibration/validation observation overlap")
    if (
        split.train
        and split.calibration
        and max(o.observed_at_ms for o in split.train)
        > min(o.observed_at_ms for o in split.calibration)
    ):
        raise SplitInvariantError("calibration fold starts before training ends")
    if split.calibration and split.validation:
        if max(o.observed_at_ms for o in split.calibration) > min(
            o.observed_at_ms for o in split.validation
        ):
            raise SplitInvariantError("validation starts before calibration fold ends")
    elif (
        split.train
        and split.validation
        and max(o.observed_at_ms for o in split.train)
        > min(o.observed_at_ms for o in split.validation)
    ):
        raise SplitInvariantError("validation starts before training ends")


def temporal_train_calibration_validation_split(
    dataset: ExperimentalFeatureDataset,
    *,
    calibration_fraction: float = DEFAULT_CALIBRATION_FRACTION,
    validation_fraction: float = DEFAULT_VALIDATION_FRACTION,
) -> TemporalThreeWaySplit:
    """earliest -> train, middle -> calibration, latest -> validation. No
    row in calibration or validation can ever influence the model; no row
    in validation can ever influence the calibrator.
    """
    if not (0.0 < calibration_fraction < 1.0) or not (0.0 < validation_fraction < 1.0):
        raise ValueError("calibration_fraction and validation_fraction must be in (0, 1)")
    if calibration_fraction + validation_fraction >= 1.0:
        raise ValueError("calibration_fraction + validation_fraction leaves no room for training")

    ordered = dataset.sorted_by_time()
    n = len(ordered)
    val_cut = _cut_before(ordered, int(n * (1.0 - validation_fraction)))
    cal_cut = _cut_before(ordered, int(n * (1.0 - validation_fraction - calibration_fraction)))
    cal_cut = min(cal_cut, val_cut)  # heavy ties can otherwise push cal_cut past val_cut

    split = TemporalThreeWaySplit(
        train=ordered[:cal_cut],
        calibration=ordered[cal_cut:val_cut],
        validation=ordered[val_cut:],
        train_period_ms=_span(ordered[:cal_cut]),
        calibration_period_ms=_span(ordered[cal_cut:val_cut]),
        validation_period_ms=_span(ordered[val_cut:]),
    )
    _verify_three_way(split)
    return split


# -- calibrators: fit on the calibration fold only ------------------------------------


class LinearCalibrator:
    """C1 — `observed ~ raw_prediction`, ordinary least squares on exactly
    one feature. Fit only on the calibration fold; the model itself never
    changes.
    """

    def __init__(self, model: object) -> None:
        self._model = model

    @classmethod
    def fit(cls, raw_predictions: Sequence[float], observed: Sequence[float]) -> LinearCalibrator:
        from sklearn.linear_model import LinearRegression

        model = LinearRegression()
        model.fit([[r] for r in raw_predictions], observed)
        return cls(model)

    def predict(self, raw_predictions: Sequence[float]) -> list[float]:
        return [float(v) for v in self._model.predict([[r] for r in raw_predictions])]  # type: ignore[attr-defined]


class IsotonicCalibrator:
    """C2 — monotonic calibration. `out_of_bounds="clip"` clips the *input*
    raw prediction into the range seen during calibration fit before
    interpolating — an isotonic step function cannot extrapolate
    meaningfully past its training domain; this is a modeling choice about
    the calibrator's input domain, not the silent [0,100] output clipping
    the task forbids (`range_diagnostic` in experiment_decision.py is the
    place that reports output-range behavior).
    """

    def __init__(self, model: object) -> None:
        self._model = model

    @classmethod
    def fit(cls, raw_predictions: Sequence[float], observed: Sequence[float]) -> IsotonicCalibrator:
        from sklearn.isotonic import IsotonicRegression

        model = IsotonicRegression(out_of_bounds="clip")
        model.fit(raw_predictions, observed)
        return cls(model)

    def predict(self, raw_predictions: Sequence[float]) -> list[float]:
        return [float(v) for v in self._model.predict(raw_predictions)]  # type: ignore[attr-defined]


class CalibrationMethod(StrEnum):
    NONE = "c0_none"
    LINEAR = "c1_linear"
    ISOTONIC = "c2_isotonic"


class CalibrationStatus(StrEnum):
    EVALUATED = "evaluated"
    NOT_EVALUABLE = "not_evaluable"


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    method: CalibrationMethod
    status: CalibrationStatus
    reason: str | None
    predictions: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class CalibrationRun:
    split: TemporalThreeWaySplit
    raw_calibration_predictions: tuple[float, ...]
    raw_validation_predictions: tuple[float, ...]
    results: dict[CalibrationMethod, CalibrationResult]


def fit_and_apply_calibrators(
    split: TemporalThreeWaySplit,
    *,
    seed: int,
    family: FeatureFamily = FeatureFamily.F2_FULL_COVARIATES,
) -> CalibrationRun:
    if not split.is_usable:
        raise ValueError(
            "cannot calibrate over an unusable split (empty train/calibration/validation)"
        )

    feature_space = FittedFeatureSpace.fit(split.train, family)
    model = LightGBMModel.fit(split.train, feature_space, seed=seed)
    raw_calibration = tuple(model.predict(split.calibration))
    raw_validation = tuple(model.predict(split.validation))
    calibration_observed = [o.y_rank_percent for o in split.calibration]

    results: dict[CalibrationMethod, CalibrationResult] = {
        CalibrationMethod.NONE: CalibrationResult(
            CalibrationMethod.NONE, CalibrationStatus.EVALUATED, None, raw_validation
        )
    }

    linear = LinearCalibrator.fit(raw_calibration, calibration_observed)
    results[CalibrationMethod.LINEAR] = CalibrationResult(
        CalibrationMethod.LINEAR,
        CalibrationStatus.EVALUATED,
        None,
        tuple(linear.predict(raw_validation)),
    )

    if len(split.calibration) < MIN_CALIBRATION_ROWS_FOR_ISOTONIC:
        results[CalibrationMethod.ISOTONIC] = CalibrationResult(
            CalibrationMethod.ISOTONIC,
            CalibrationStatus.NOT_EVALUABLE,
            f"calibration fold has {len(split.calibration)} rows, below the "
            f"{MIN_CALIBRATION_ROWS_FOR_ISOTONIC}-row floor for a monotonic fit",
            (),
        )
    else:
        isotonic = IsotonicCalibrator.fit(raw_calibration, calibration_observed)
        results[CalibrationMethod.ISOTONIC] = CalibrationResult(
            CalibrationMethod.ISOTONIC,
            CalibrationStatus.EVALUATED,
            None,
            tuple(isotonic.predict(raw_validation)),
        )

    return CalibrationRun(split, raw_calibration, raw_validation, results)


# -- combined raw+calibrated diagnostics -----------------------------------------------


def mean_signed_error(y_true: Sequence[float], y_pred: Sequence[float]) -> float:
    """Bias: positive means the predictor overshoots on average."""
    if len(y_true) != len(y_pred):
        raise ValueError("mean_signed_error requires equal-length sequences")
    if not y_true:
        raise ValueError("mean_signed_error of an empty sequence")
    return statistics.mean(p - t for t, p in zip(y_true, y_pred, strict=True))


@dataclass(frozen=True, slots=True)
class ObservedBucketRow:
    bucket: str
    n: int
    mean_observed: float
    mean_raw: float
    raw_bias: float
    mean_calibrated: float
    calibrated_bias: float
    raw_mae: float
    calibrated_mae: float


def calibration_by_observed_bucket(
    observations: Sequence[ExperimentalObservation],
    raw_predictions: Sequence[float],
    calibrated_predictions: Sequence[float],
) -> list[ObservedBucketRow]:
    """Grouped by the *true* rankPercent bucket — never hides `n`."""
    groups: dict[str, list[int]] = {}
    for i, o in enumerate(observations):
        groups.setdefault(percentile_bucket(o.y_rank_percent), []).append(i)

    from botgitgud.phase4.experiment import PERCENTILE_BUCKETS

    rows = []
    for bucket in PERCENTILE_BUCKETS:
        indices = groups.get(bucket)
        if not indices:
            continue
        observed = [observations[i].y_rank_percent for i in indices]
        raw = [raw_predictions[i] for i in indices]
        calibrated = [calibrated_predictions[i] for i in indices]
        rows.append(
            ObservedBucketRow(
                bucket=bucket,
                n=len(indices),
                mean_observed=statistics.mean(observed),
                mean_raw=statistics.mean(raw),
                raw_bias=mean_signed_error(observed, raw),
                mean_calibrated=statistics.mean(calibrated),
                calibrated_bias=mean_signed_error(observed, calibrated),
                raw_mae=regression_metrics(observed, raw).mae,
                calibrated_mae=regression_metrics(observed, calibrated).mae,
            )
        )
    return rows


@dataclass(frozen=True, slots=True)
class GroupBiasRow:
    key: str
    n: int
    raw_bias: float
    calibrated_bias: float


def bias_by_group(
    observations: Sequence[ExperimentalObservation],
    raw_predictions: Sequence[float],
    calibrated_predictions: Sequence[float],
    key_fn: object,
) -> list[GroupBiasRow]:
    groups: dict[object, list[int]] = {}
    for i, o in enumerate(observations):
        groups.setdefault(key_fn(o), []).append(i)  # type: ignore[operator]
    rows = []
    for key, indices in groups.items():
        observed = [observations[i].y_rank_percent for i in indices]
        raw = [raw_predictions[i] for i in indices]
        calibrated = [calibrated_predictions[i] for i in indices]
        rows.append(
            GroupBiasRow(
                key=str(key),
                n=len(indices),
                raw_bias=mean_signed_error(observed, raw),
                calibrated_bias=mean_signed_error(observed, calibrated),
            )
        )
    return rows


# -- cross-fitted diagnostic (offline only, never a substitute for the temporal gate) --


def cross_fitted_raw_diagnostic(
    dataset: ExperimentalFeatureDataset,
    *,
    seed: int,
    k: int = CROSS_FIT_K,
    family: FeatureFamily = FeatureFamily.F2_FULL_COVARIATES,
) -> RegressionMetrics:
    """K-fold, deterministic non-temporal partition (row order by natural
    key, assigned round-robin) — every prediction comes from a model that
    never saw its own label. This is a *diagnostic*: it uses more of the
    dataset than the single temporal validation slice, at the cost of not
    respecting time order, so it must never replace the temporal gate
    above — only corroborate or contradict it.
    """
    ordered = sorted(dataset.observations, key=lambda o: o.observation_key)
    folds = [ordered[i::k] for i in range(k)]
    pooled_true: list[float] = []
    pooled_pred: list[float] = []
    for i in range(k):
        validation = folds[i]
        train = [o for j, fold in enumerate(folds) if j != i for o in fold]
        if not train or not validation:
            continue
        feature_space = FittedFeatureSpace.fit(train, family)
        model = LightGBMModel.fit(train, feature_space, seed=seed)
        pooled_true.extend(o.y_rank_percent for o in validation)
        pooled_pred.extend(model.predict(validation))
    return regression_metrics(pooled_true, pooled_pred)


# -- confidence framework (not exposed to the bot) -------------------------------------


class ConfidenceLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


EXTREME_PREDICTED_BUCKETS: frozenset[str] = frozenset({"00-20", "80-100"})
# Thresholds are row counts in train+calibration (what the deployed model
# and calibrator actually learned from) for the observation's own
# spec/encounter — chosen above the median but below the max measured in
# this campaign (specs: 8-39 rows; encounters: 34-90 rows), so HIGH is
# reserved for the better-covered half, never handed out by default.
HIGH_SPEC_COVERAGE = 30
HIGH_ENCOUNTER_COVERAGE = 50
MEDIUM_SPEC_COVERAGE = 15
MEDIUM_ENCOUNTER_COVERAGE = 30


def confidence_level(
    *, predicted_bucket: str, spec_coverage: int, encounter_coverage: int
) -> ConfidenceLevel:
    """Deterministic, from measurable coverage only — never HIGH for an
    extreme predicted band (task brief: don't over-claim confidence at the
    tails, regardless of how much interior data exists elsewhere).
    """
    is_extreme = predicted_bucket in EXTREME_PREDICTED_BUCKETS
    if (
        not is_extreme
        and spec_coverage >= HIGH_SPEC_COVERAGE
        and encounter_coverage >= HIGH_ENCOUNTER_COVERAGE
    ):
        return ConfidenceLevel.HIGH
    if spec_coverage >= MEDIUM_SPEC_COVERAGE or encounter_coverage >= MEDIUM_ENCOUNTER_COVERAGE:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW
