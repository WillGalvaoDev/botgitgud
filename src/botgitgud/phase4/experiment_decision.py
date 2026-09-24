"""Statistical Architecture Decision Gate (docs/phase4.md):
leave-one-spec/encounter-out for `MODEL_GLOBAL`, macro/micro aggregation,
paired bootstrap of model deltas, seed sensitivity, error/calibration/range
diagnostics, and Phase4Target density from the frozen plan. Offline, over
whatever has already been `completed`.

Builds on SAE.8 (`experiment_models.py`, `experiment_evaluate.py`) rather
than duplicating it — `FittedFeatureSpace`, `MedianBaseline`,
`LinearRegressionModel`, `LightGBMModel`, `RidgeRegressionModel`,
`bootstrap_mae_spearman` and `paired_bootstrap_delta` are reused as-is.
This module only adds the *per-fold* (not pooled-across-folds) reporting
shape SAE.8's matrix didn't need, plus descriptive diagnostics that don't
fit the (granularity x split x family x model) cell abstraction.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from botgitgud.phase4.experiment import PERCENTILE_BUCKETS, percentile_bucket
from botgitgud.phase4.experiment_metrics import (
    MedianBaseline,
    RegressionMetrics,
    regression_metrics,
)
from botgitgud.phase4.experiment_models import (
    BootstrapCI,
    FeatureFamily,
    FittedFeatureSpace,
    LightGBMModel,
    LinearRegressionModel,
    RidgeRegressionModel,
    bootstrap_mae_spearman,
    paired_bootstrap_delta,
)
from botgitgud.phase4.experiment_splits import held_out_encounter_split, held_out_spec_split
from botgitgud.phase4.experimental_dataset import (
    ExperimentalFeatureDataset,
    ExperimentalObservation,
)

# Reused as the reporting floor for a leave-one-out fold, not just for
# bootstrap measurability — a fold with fewer rows than this is flagged
# insufficient_data rather than silently included in macro/micro averages.
MIN_VALIDATION_ROWS_FOR_HOLDOUT = 10
SEED_SENSITIVITY_SEEDS: tuple[int, ...] = (20260821, 20260822, 20260823, 20260824, 20260825)


def _percentile(sorted_values: Sequence[float], pct: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * pct / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_values[int(k)]
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


# -- leave-one-{spec,encounter}-out, MODEL_GLOBAL only ---------------------------


class HoldoutStatus(StrEnum):
    OK = "ok"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True, slots=True)
class FoldPredictions:
    y_true: tuple[float, ...]
    predictions: dict[str, tuple[float, ...]]  # model name -> predictions


@dataclass(frozen=True, slots=True)
class GroupHoldoutResult:
    key: str
    n_train: int
    n_validation: int
    status: HoldoutStatus
    by_family: dict[FeatureFamily, FoldPredictions]


def _fit_predict_all_models(
    train: Sequence[ExperimentalObservation],
    validation: Sequence[ExperimentalObservation],
    family: FeatureFamily,
    *,
    seed: int,
) -> FoldPredictions:
    feature_space = FittedFeatureSpace.fit(train, family)
    y_true = tuple(o.y_rank_percent for o in validation)
    predictions = {
        "baseline_0": tuple(MedianBaseline.fit(train).predict(validation)),
        "baseline_1": tuple(LinearRegressionModel.fit(train, feature_space).predict(validation)),
        "lightgbm": tuple(LightGBMModel.fit(train, feature_space, seed=seed).predict(validation)),
    }
    return FoldPredictions(y_true, predictions)


def leave_one_out_global(
    dataset: ExperimentalFeatureDataset,
    *,
    dimension: str,
    families: Sequence[FeatureFamily],
    seed: int,
) -> list[GroupHoldoutResult]:
    """`dimension` is `"spec"` or `"encounter"`. Always `MODEL_GLOBAL`: one
    model trained on everyone else, evaluated on exactly the retained
    spec/encounter — the task brief's leave-one-out protocol, not SAE.8's
    pooled-across-folds S3/S4.
    """
    if dimension not in ("spec", "encounter"):
        raise ValueError(f"dimension must be 'spec' or 'encounter', got {dimension!r}")

    keys: Sequence[object] = (
        sorted(dataset.specs) if dimension == "spec" else sorted(dataset.encounters)
    )
    results: list[GroupHoldoutResult] = []
    for key in keys:
        split = (
            held_out_spec_split(dataset, spec_key=str(key))
            if dimension == "spec"
            else held_out_encounter_split(dataset, encounter_id=int(key))  # type: ignore[arg-type]
        )
        if not split.is_usable:
            results.append(
                GroupHoldoutResult(
                    str(key),
                    len(split.train),
                    len(split.validation),
                    HoldoutStatus.INSUFFICIENT_DATA,
                    {},
                )
            )
            continue
        status = (
            HoldoutStatus.OK
            if len(split.validation) >= MIN_VALIDATION_ROWS_FOR_HOLDOUT
            else HoldoutStatus.INSUFFICIENT_DATA
        )
        by_family = {
            family: _fit_predict_all_models(split.train, split.validation, family, seed=seed)
            for family in families
        }
        results.append(
            GroupHoldoutResult(str(key), len(split.train), len(split.validation), status, by_family)
        )
    return results


def group_metric(
    result: GroupHoldoutResult, family: FeatureFamily, model: str
) -> RegressionMetrics | None:
    if result.status is HoldoutStatus.INSUFFICIENT_DATA or family not in result.by_family:
        return None
    fp = result.by_family[family]
    return regression_metrics(fp.y_true, fp.predictions[model])


def group_bootstrap(
    result: GroupHoldoutResult,
    family: FeatureFamily,
    model: str,
    *,
    seed: int,
    n_resamples: int = 1000,
) -> tuple[BootstrapCI, BootstrapCI]:
    if result.status is HoldoutStatus.INSUFFICIENT_DATA or family not in result.by_family:
        empty = BootstrapCI(None, None, None, 0, False)
        return empty, empty
    fp = result.by_family[family]
    return bootstrap_mae_spearman(
        fp.y_true, fp.predictions[model], seed=seed, n_resamples=n_resamples
    )


def group_paired_delta(
    result: GroupHoldoutResult,
    family: FeatureFamily,
    model_a: str,
    model_b: str,
    *,
    seed: int,
    n_resamples: int = 1000,
) -> tuple[BootstrapCI, BootstrapCI]:
    """ΔMAE/ΔSpearman of `model_a` vs `model_b` on exactly this group's
    validation rows — e.g. LightGBM vs Baseline 0 for one held-out spec.
    """
    if result.status is HoldoutStatus.INSUFFICIENT_DATA or family not in result.by_family:
        empty = BootstrapCI(None, None, None, 0, False)
        return empty, empty
    fp = result.by_family[family]
    return paired_bootstrap_delta(
        fp.y_true,
        fp.predictions[model_a],
        fp.predictions[model_b],
        seed=seed,
        n_resamples=n_resamples,
    )


# -- macro vs micro ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MacroMicroSummary:
    micro: RegressionMetrics
    n_groups_total: int
    n_groups_ok: int
    macro_mean_mae: float | None
    macro_median_mae: float | None
    macro_worst_mae: float | None
    macro_p90_mae: float | None  # worst decile of per-group MAE across groups
    macro_mean_spearman: float | None
    worst_group_key: str | None


def summarize_macro_micro(
    results: Sequence[GroupHoldoutResult], family: FeatureFamily, model: str
) -> MacroMicroSummary:
    ok = [r for r in results if r.status is HoldoutStatus.OK and family in r.by_family]
    pooled_true: list[float] = []
    pooled_pred: list[float] = []
    per_group: list[tuple[str, float]] = []
    rhos: list[float] = []
    for r in ok:
        fp = r.by_family[family]
        pooled_true.extend(fp.y_true)
        pooled_pred.extend(fp.predictions[model])
        m = regression_metrics(fp.y_true, fp.predictions[model])
        per_group.append((r.key, m.mae))
        if m.spearman is not None:
            rhos.append(m.spearman)

    micro = regression_metrics(pooled_true, pooled_pred)
    if not per_group:
        return MacroMicroSummary(micro, len(results), 0, None, None, None, None, None, None)

    maes = sorted(v for _, v in per_group)
    worst_key = max(per_group, key=lambda kv: kv[1])[0]
    return MacroMicroSummary(
        micro=micro,
        n_groups_total=len(results),
        n_groups_ok=len(ok),
        macro_mean_mae=statistics.mean(maes),
        macro_median_mae=statistics.median(maes),
        macro_worst_mae=maes[-1],
        macro_p90_mae=_percentile(maes, 90),
        macro_mean_spearman=statistics.mean(rhos) if rhos else None,
        worst_group_key=worst_key,
    )


# -- seed sensitivity ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeedSensitivityResult:
    seeds: tuple[int, ...]
    mae_values: tuple[float, ...]
    spearman_values: tuple[float | None, ...]
    mae_mean: float
    mae_std: float
    mae_min: float
    mae_max: float
    spearman_mean: float | None
    spearman_std: float | None
    spearman_min: float | None
    spearman_max: float | None


def seed_sensitivity(
    train: Sequence[ExperimentalObservation],
    validation: Sequence[ExperimentalObservation],
    family: FeatureFamily,
    *,
    seeds: Sequence[int] = SEED_SENSITIVITY_SEEDS,
) -> SeedSensitivityResult:
    feature_space = FittedFeatureSpace.fit(train, family)
    y_true = [o.y_rank_percent for o in validation]
    maes: list[float] = []
    rhos: list[float | None] = []
    for seed in seeds:
        preds = LightGBMModel.fit(train, feature_space, seed=seed).predict(validation)
        m = regression_metrics(y_true, preds)
        maes.append(m.mae)
        rhos.append(m.spearman)
    measured_rhos = [r for r in rhos if r is not None]
    return SeedSensitivityResult(
        seeds=tuple(seeds),
        mae_values=tuple(maes),
        spearman_values=tuple(rhos),
        mae_mean=statistics.mean(maes),
        mae_std=statistics.pstdev(maes),
        mae_min=min(maes),
        mae_max=max(maes),
        spearman_mean=statistics.mean(measured_rhos) if measured_rhos else None,
        spearman_std=statistics.pstdev(measured_rhos) if len(measured_rhos) > 1 else None,
        spearman_min=min(measured_rhos) if measured_rhos else None,
        spearman_max=max(measured_rhos) if measured_rhos else None,
    )


# -- error distribution / calibration / range diagnostics --------------------------


@dataclass(frozen=True, slots=True)
class ErrorDistribution:
    n: int
    median_abs_error: float
    p75_abs_error: float
    p90_abs_error: float
    p95_abs_error: float
    max_abs_error: float
    bias_by_bucket: dict[str, float]
    bias_by_spec: dict[str, float]
    bias_by_encounter: dict[int, float]


def _bias_by(
    observations: Sequence[ExperimentalObservation],
    predictions: Sequence[float],
    key_fn: object,
) -> dict[object, float]:
    groups: dict[object, list[float]] = {}
    for o, p in zip(observations, predictions, strict=True):
        groups.setdefault(key_fn(o), []).append(p - o.y_rank_percent)  # type: ignore[operator]
    return {k: statistics.mean(v) for k, v in groups.items()}


def error_distribution(
    observations: Sequence[ExperimentalObservation], predictions: Sequence[float]
) -> ErrorDistribution:
    abs_errors = sorted(
        abs(o.y_rank_percent - p) for o, p in zip(observations, predictions, strict=True)
    )
    return ErrorDistribution(
        n=len(observations),
        median_abs_error=_percentile(abs_errors, 50),
        p75_abs_error=_percentile(abs_errors, 75),
        p90_abs_error=_percentile(abs_errors, 90),
        p95_abs_error=_percentile(abs_errors, 95),
        max_abs_error=abs_errors[-1],
        bias_by_bucket={
            str(k): v
            for k, v in _bias_by(
                observations, predictions, lambda o: percentile_bucket(o.y_rank_percent)
            ).items()
        },
        bias_by_spec={
            str(k): v for k, v in _bias_by(observations, predictions, lambda o: o.spec_key).items()
        },
        bias_by_encounter={
            int(k): v  # type: ignore[arg-type]
            for k, v in _bias_by(observations, predictions, lambda o: o.target.encounter_id).items()
        },
    )


@dataclass(frozen=True, slots=True)
class CalibrationRow:
    predicted_bucket: str
    n: int
    mean_predicted: float
    mean_observed: float
    bias: float


def calibration_by_predicted_bucket(
    observations: Sequence[ExperimentalObservation], predictions: Sequence[float]
) -> list[CalibrationRow]:
    """Bins by the *predicted* bucket (clipped to [0,100] only for binning,
    never for the metric) and compares against the true rankPercent
    observed in that band — a reliability diagram, not a classification.
    """
    groups: dict[str, list[tuple[float, float]]] = {}
    for o, p in zip(observations, predictions, strict=True):
        bucket = percentile_bucket(min(100.0, max(0.0, p)))
        groups.setdefault(bucket, []).append((p, o.y_rank_percent))
    rows = []
    for bucket in PERCENTILE_BUCKETS:
        pairs = groups.get(bucket)
        if not pairs:
            continue
        preds = [p for p, _ in pairs]
        truths = [t for _, t in pairs]
        mean_pred, mean_obs = statistics.mean(preds), statistics.mean(truths)
        rows.append(CalibrationRow(bucket, len(pairs), mean_pred, mean_obs, mean_pred - mean_obs))
    return rows


@dataclass(frozen=True, slots=True)
class RangeDiagnostic:
    n: int
    n_below_zero: int
    n_above_hundred: int
    min_prediction: float
    max_prediction: float
    raw_metrics: RegressionMetrics
    clipped_metrics: RegressionMetrics


def range_diagnostic(
    observations: Sequence[ExperimentalObservation], predictions: Sequence[float]
) -> RangeDiagnostic:
    y_true = [o.y_rank_percent for o in observations]
    clipped = [min(100.0, max(0.0, p)) for p in predictions]
    return RangeDiagnostic(
        n=len(predictions),
        n_below_zero=sum(1 for p in predictions if p < 0.0),
        n_above_hundred=sum(1 for p in predictions if p > 100.0),
        min_prediction=min(predictions),
        max_prediction=max(predictions),
        raw_metrics=regression_metrics(y_true, predictions),
        clipped_metrics=regression_metrics(y_true, clipped),
    )


# -- Ridge diagnostic ------------------------------------------------------------------


def ridge_diagnostic(
    train: Sequence[ExperimentalObservation],
    validation: Sequence[ExperimentalObservation],
    family: FeatureFamily,
) -> tuple[RegressionMetrics, RegressionMetrics]:
    """Returns (lightgbm_metrics, ridge_metrics) on the same validation rows
    — DIAGNOSTIC_BASELINE_RIDGE, never a replacement for Baseline 1.
    """
    feature_space = FittedFeatureSpace.fit(train, family)
    y_true = [o.y_rank_percent for o in validation]
    lgbm_preds = LightGBMModel.fit(train, feature_space, seed=SEED_SENSITIVITY_SEEDS[0]).predict(
        validation
    )
    ridge_preds = RidgeRegressionModel.fit(train, feature_space).predict(validation)
    return regression_metrics(y_true, lgbm_preds), regression_metrics(y_true, ridge_preds)
