"""`experiment-calibrate` — Phase 4 Global Model Validation & Calibration
Gate (docs/fase4-global-model-validation.md). Freezes the already-decided
candidate (MODEL_GLOBAL, F2, LightGBM) and asks whether its raw
predictions are calibrated, whether a simple auditable calibrator helps
without leaking validation labels, and what confidence should be
attributed to a prediction.

Strictly offline, same contract as the other `experiment-*` commands:
opens `Store` directly and never constructs a `WclClient`.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict

from botgitgud.config import Settings
from botgitgud.ingest.store import Store
from botgitgud.phase4.experiment import percentile_bucket
from botgitgud.phase4.experiment_calibration import (
    CalibrationMethod,
    CalibrationRun,
    ConfidenceLevel,
    GroupBiasRow,
    ObservedBucketRow,
    TemporalThreeWaySplit,
    bias_by_group,
    calibration_by_observed_bucket,
    confidence_level,
    cross_fitted_raw_diagnostic,
    fit_and_apply_calibrators,
    mean_signed_error,
    temporal_train_calibration_validation_split,
)
from botgitgud.phase4.experiment_calibration_store import ExperimentCalibrationStore
from botgitgud.phase4.experiment_decision import (
    CalibrationRow,
    RangeDiagnostic,
    calibration_by_predicted_bucket,
    range_diagnostic,
)
from botgitgud.phase4.experiment_evaluate import DEFAULT_SEED, dataset_hash
from botgitgud.phase4.experiment_metrics import RegressionMetrics, regression_metrics
from botgitgud.phase4.experiment_store import ExperimentCampaignStore
from botgitgud.phase4.experimental_dataset import (
    ExperimentalDatasetBuilder,
    ExperimentalObservation,
)

BuildStore = Callable[[Settings], Store]


def _default_build_store(settings: Settings) -> Store:
    return Store(settings.data_dir)


def add_experiment_calibrate_parser(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
    *,
    build_store: BuildStore = _default_build_store,
) -> None:
    p = sub.add_parser(
        "experiment-calibrate",
        help="Phase 4 Global Model Validation & Calibration Gate: RAW vs CALIBRATED "
        "(linear/isotonic), temporal train/calibration/validation. Offline.",
    )
    p.add_argument("--campaign", required=True, help="campaign_id congelado.")
    p.set_defaults(func=lambda args: cmd_experiment_calibrate(args, build_store=build_store))


def cmd_experiment_calibrate(args: argparse.Namespace, *, build_store: BuildStore) -> int:
    settings = Settings()  # type: ignore[call-arg]  # populada a partir do .env em runtime
    store = build_store(settings)
    try:
        stored = ExperimentCampaignStore(store).get(args.campaign)
        if stored is None:
            sys.stderr.write(f"erro: campaign desconhecida: {args.campaign}\n")
            return 1
        dataset = ExperimentalDatasetBuilder(store).build(campaign_id=args.campaign)
        if dataset.is_empty:
            sys.stderr.write("erro: nenhuma observacao completed para esta campaign.\n")
            return 1

        seed = DEFAULT_SEED
        split = temporal_train_calibration_validation_split(dataset)
        if not split.is_usable:
            sys.stderr.write(
                "erro: split temporal train/calibration/validation nao usavel "
                "(algum lado ficou vazio) — dataset insuficiente para este gate.\n"
            )
            return 1

        run = fit_and_apply_calibrators(split, seed=seed)
        y_true_val = [o.y_rank_percent for o in split.validation]

        metrics_by_method = {
            method: _full_metrics(y_true_val, result.predictions)
            for method, result in run.results.items()
            if result.predictions
        }

        raw_preds = run.raw_validation_predictions
        linear_preds = run.results[CalibrationMethod.LINEAR].predictions
        observed_bucket_rows = calibration_by_observed_bucket(
            split.validation, raw_preds, linear_preds
        )

        predicted_band_raw = calibration_by_predicted_bucket(split.validation, raw_preds)
        predicted_band_linear = calibration_by_predicted_bucket(split.validation, linear_preds)

        spec_bias = bias_by_group(split.validation, raw_preds, linear_preds, lambda o: o.spec_key)
        encounter_bias = bias_by_group(
            split.validation, raw_preds, linear_preds, lambda o: o.target.encounter_id
        )

        raw_range = range_diagnostic(split.validation, raw_preds)
        linear_range = range_diagnostic(split.validation, linear_preds)

        cross_fit = cross_fitted_raw_diagnostic(dataset, seed=seed)

        confidence_counts = _confidence_distribution(split, linear_preds)

        run_id = f"calibrate-{uuid.uuid4().hex[:16]}"
        fingerprint = dataset_hash(dataset)
        payload = _build_payload(
            split,
            run,
            metrics_by_method,
            observed_bucket_rows,
            predicted_band_raw,
            predicted_band_linear,
            spec_bias,
            encounter_bias,
            raw_range,
            linear_range,
            cross_fit,
            confidence_counts,
        )
        ExperimentCalibrationStore(store).save(
            run_id,
            campaign_id=args.campaign,
            dataset_fingerprint=fingerprint,
            dataset_row_count=len(dataset),
            seed=seed,
            n_train=len(split.train),
            n_calibration=len(split.calibration),
            n_validation=len(split.validation),
            payload=payload,
        )

        _print_summary(
            run_id,
            fingerprint,
            split,
            metrics_by_method,
            run,
            observed_bucket_rows,
            spec_bias,
            encounter_bias,
            raw_range,
            linear_range,
            cross_fit,
            confidence_counts,
        )
        return 0
    finally:
        store.close()


def _full_metrics(y_true: list[float], y_pred: tuple[float, ...]) -> dict[str, float | None]:
    m: RegressionMetrics = regression_metrics(y_true, list(y_pred))
    abs_errors = sorted(abs(t - p) for t, p in zip(y_true, y_pred, strict=True))
    n = len(abs_errors)

    def pct(p: float) -> float:
        if n == 1:
            return abs_errors[0]
        import math

        k = (n - 1) * p / 100.0
        lo, hi = math.floor(k), math.ceil(k)
        if lo == hi:
            return abs_errors[int(k)]
        return abs_errors[lo] + (abs_errors[hi] - abs_errors[lo]) * (k - lo)

    return {
        "mae": m.mae,
        "rmse": m.rmse,
        "spearman": m.spearman,
        "median_abs_error": pct(50),
        "p75_abs_error": pct(75),
        "p90_abs_error": pct(90),
        "p95_abs_error": pct(95),
        "max_abs_error": abs_errors[-1] if abs_errors else None,
        "bias": mean_signed_error(y_true, list(y_pred)),
    }


def _confidence_distribution(
    split: TemporalThreeWaySplit, predictions: tuple[float, ...]
) -> dict[str, int]:
    validation = split.validation
    reference: tuple[ExperimentalObservation, ...] = split.train + split.calibration
    spec_counts: dict[str, int] = {}
    encounter_counts: dict[int, int] = {}
    for o in reference:
        spec_counts[o.spec_key] = spec_counts.get(o.spec_key, 0) + 1
        encounter_counts[o.target.encounter_id] = encounter_counts.get(o.target.encounter_id, 0) + 1

    counts = {level.value: 0 for level in ConfidenceLevel}
    for o, pred in zip(validation, predictions, strict=True):
        clipped = min(100.0, max(0.0, pred))
        bucket = percentile_bucket(clipped)
        level = confidence_level(
            predicted_bucket=bucket,
            spec_coverage=spec_counts.get(o.spec_key, 0),
            encounter_coverage=encounter_counts.get(o.target.encounter_id, 0),
        )
        counts[level.value] += 1
    return counts


def _range_dict(diag: RangeDiagnostic) -> dict[str, float]:
    return {
        "n_below_zero": diag.n_below_zero,
        "n_above_hundred": diag.n_above_hundred,
        "min": diag.min_prediction,
        "max": diag.max_prediction,
    }


def _build_payload(
    split: TemporalThreeWaySplit,
    run: CalibrationRun,
    metrics_by_method: dict[CalibrationMethod, dict[str, float | None]],
    observed_bucket_rows: Sequence[ObservedBucketRow],
    predicted_band_raw: list[CalibrationRow],
    predicted_band_linear: list[CalibrationRow],
    spec_bias: Sequence[GroupBiasRow],
    encounter_bias: Sequence[GroupBiasRow],
    raw_range: RangeDiagnostic,
    linear_range: RangeDiagnostic,
    cross_fit: RegressionMetrics,
    confidence_counts: dict[str, int],
) -> dict[str, object]:
    return {
        "train_period_ms": split.train_period_ms,
        "calibration_period_ms": split.calibration_period_ms,
        "validation_period_ms": split.validation_period_ms,
        "metrics_by_method": {k.value: v for k, v in metrics_by_method.items()},
        "isotonic_status": run.results[CalibrationMethod.ISOTONIC].status.value,
        "isotonic_reason": run.results[CalibrationMethod.ISOTONIC].reason,
        "observed_bucket": [asdict(r) for r in observed_bucket_rows],
        "predicted_band_raw": [asdict(r) for r in predicted_band_raw],
        "predicted_band_linear": [asdict(r) for r in predicted_band_linear],
        "spec_bias": [asdict(r) for r in spec_bias],
        "encounter_bias": [asdict(r) for r in encounter_bias],
        "raw_range": _range_dict(raw_range),
        "linear_range": _range_dict(linear_range),
        "cross_fitted_diagnostic": {
            "n": cross_fit.n,
            "mae": cross_fit.mae,
            "rmse": cross_fit.rmse,
            "spearman": cross_fit.spearman,
        },
        "confidence_distribution": confidence_counts,
    }


def _print_summary(
    run_id: str,
    fingerprint: str,
    split: TemporalThreeWaySplit,
    metrics_by_method: dict[CalibrationMethod, dict[str, float | None]],
    run: CalibrationRun,
    observed_bucket_rows: Sequence[object],
    spec_bias: Sequence[object],
    encounter_bias: Sequence[object],
    raw_range: RangeDiagnostic,
    linear_range: RangeDiagnostic,
    cross_fit: RegressionMetrics,
    confidence_counts: dict[str, int],
) -> None:
    out = sys.stdout.write
    out(
        "PHASE 4 — GLOBAL MODEL VALIDATION & CALIBRATION GATE (offline, zero WCL)\n\n"
        f"  run_id ............................... {run_id}\n"
        f"  dataset_fingerprint .................. {fingerprint}\n"
        f"  train ................................ {len(split.train)} rows, "
        f"{split.train_period_ms}\n"
        f"  calibration ........................... {len(split.calibration)} rows, "
        f"{split.calibration_period_ms}\n"
        f"  validation ............................ {len(split.validation)} rows, "
        f"{split.validation_period_ms}\n\n"
    )
    for method, metrics in metrics_by_method.items():
        out(f"  {method.value:<12} {metrics}\n")
    iso_status = run.results[CalibrationMethod.ISOTONIC].status.value
    iso_reason = run.results[CalibrationMethod.ISOTONIC].reason
    out(f"\n  isotonic status: {iso_status} ({iso_reason})\n")

    out("\n  calibration by observed bucket (n, raw/calibrated bias and MAE)\n")
    for row in observed_bucket_rows:
        out(f"    {row}\n")  # type: ignore[str-format]

    out("\n  spec bias (raw vs calibrated)\n")
    for row in spec_bias:
        out(f"    {row}\n")  # type: ignore[str-format]

    out("\n  encounter bias (raw vs calibrated)\n")
    for row in encounter_bias:
        out(f"    {row}\n")  # type: ignore[str-format]

    out(f"\n  raw range: {_range_dict(raw_range)}\n")
    out(f"  linear-calibrated range: {_range_dict(linear_range)}\n")
    out(f"\n  cross-fitted diagnostic (k-fold, offline only): {cross_fit}\n")
    out(f"\n  confidence distribution (validation rows): {confidence_counts}\n\n")
    out(
        "Nenhum modelo/calibrador foi promovido a phase4_model_registry.\n"
        "API points consumidos ............... 0\n"
    )
