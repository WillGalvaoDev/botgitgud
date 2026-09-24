"""`experiment-decide` — the Statistical Architecture Decision Gate
(docs/phase4.md): leave-one-spec/encounter-out for
MODEL_GLOBAL, macro/micro aggregation, paired bootstrap, a Ridge
diagnostic, seed sensitivity, error/calibration/range diagnostics, and
Phase4Target density from the frozen plan.

Strictly offline, same contract as `cli_experiment_evaluate.py`: opens
`Store` directly and never constructs a `WclClient`.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Callable, Sequence

from botgitgud.config import Settings
from botgitgud.ingest.store import Store
from botgitgud.phase4.experiment_decision import (
    MacroMicroSummary,
    calibration_by_predicted_bucket,
    error_distribution,
    leave_one_out_global,
    range_diagnostic,
    ridge_diagnostic,
    seed_sensitivity,
    summarize_macro_micro,
)
from botgitgud.phase4.experiment_decision_store import (
    ExperimentArchitectureDecisionStore,
    holdout_results_to_json,
)
from botgitgud.phase4.experiment_density import (
    coverage_completed_vs_planned,
    phase4_target_density,
)
from botgitgud.phase4.experiment_evaluate import ALL_FAMILIES, DEFAULT_SEED, dataset_hash
from botgitgud.phase4.experiment_metrics import MedianBaseline
from botgitgud.phase4.experiment_models import (
    FeatureFamily,
    FittedFeatureSpace,
    LightGBMModel,
    LinearRegressionModel,
    paired_bootstrap_delta,
)
from botgitgud.phase4.experiment_splits import (
    DEFAULT_VALIDATION_FRACTION,
    temporal_within_target_split,
)
from botgitgud.phase4.experiment_store import ExperimentCampaignStore
from botgitgud.phase4.experimental_dataset import ExperimentalDatasetBuilder

BuildStore = Callable[[Settings], Store]
_MODELS = ("baseline_0", "baseline_1", "lightgbm")


def _default_build_store(settings: Settings) -> Store:
    return Store(settings.data_dir)


def add_experiment_decide_parser(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
    *,
    build_store: BuildStore = _default_build_store,
) -> None:
    p = sub.add_parser(
        "experiment-decide",
        help="Statistical Architecture Decision Gate: leave-one-out spec/encounter, "
        "macro/micro, Ridge diagnostic, seed sensitivity, calibration. Offline.",
    )
    p.add_argument("--campaign", required=True, help="campaign_id congelado.")
    p.set_defaults(func=lambda args: cmd_experiment_decide(args, build_store=build_store))


def cmd_experiment_decide(args: argparse.Namespace, *, build_store: BuildStore) -> int:
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
        spec_holdout = leave_one_out_global(
            dataset, dimension="spec", families=ALL_FAMILIES, seed=seed
        )
        encounter_holdout = leave_one_out_global(
            dataset, dimension="encounter", families=ALL_FAMILIES, seed=seed
        )

        macro_micro = {
            f"{dim}/{family.value}/{model}": summarize_macro_micro(results, family, model)
            for dim, results in (("spec", spec_holdout), ("encounter", encounter_holdout))
            for family in ALL_FAMILIES
            for model in _MODELS
        }

        s1 = temporal_within_target_split(dataset, validation_fraction=DEFAULT_VALIDATION_FRACTION)
        if not s1.is_usable:
            sys.stderr.write(
                "erro: split temporal S1 nao usavel (treino ou validacao vazios) — "
                "dataset insuficiente para os diagnosticos canonicos (S1).\n"
            )
            return 1
        fs_f2 = FittedFeatureSpace.fit(s1.train, FeatureFamily.F2_FULL_COVARIATES)
        fs_f1 = FittedFeatureSpace.fit(s1.train, FeatureFamily.F1_CONTROLLABLE_ONLY)
        preds_f2 = LightGBMModel.fit(s1.train, fs_f2, seed=seed).predict(s1.validation)
        preds_f1 = LightGBMModel.fit(s1.train, fs_f1, seed=seed).predict(s1.validation)
        preds_b0 = MedianBaseline.fit(s1.train).predict(s1.validation)
        preds_b1 = LinearRegressionModel.fit(s1.train, fs_f2).predict(s1.validation)
        y_true = [o.y_rank_percent for o in s1.validation]

        seeds = seed_sensitivity(s1.train, s1.validation, FeatureFamily.F2_FULL_COVARIATES)
        errdist = error_distribution(s1.validation, preds_f2)
        calibration = calibration_by_predicted_bucket(s1.validation, preds_f2)
        rangediag = range_diagnostic(s1.validation, preds_f2)
        lgbm_metrics, ridge_metrics = ridge_diagnostic(
            s1.train, s1.validation, FeatureFamily.F2_FULL_COVARIATES
        )
        paired_lgbm_vs_b0 = paired_bootstrap_delta(y_true, preds_f2, preds_b0, seed=seed)
        paired_lgbm_vs_b1 = paired_bootstrap_delta(y_true, preds_f2, preds_b1, seed=seed)
        paired_f2_vs_f1 = paired_bootstrap_delta(y_true, preds_f2, preds_f1, seed=seed)

        density_current = phase4_target_density(stored, only_completed=True)
        density_planned = phase4_target_density(stored, only_completed=False)
        coverage = coverage_completed_vs_planned(stored)

        run_id = f"decide-{uuid.uuid4().hex[:16]}"
        fingerprint = dataset_hash(dataset)
        payload = {
            "spec_holdout": holdout_results_to_json(spec_holdout),
            "encounter_holdout": holdout_results_to_json(encounter_holdout),
            "macro_micro": {k: _summary_to_dict(v) for k, v in macro_micro.items()},
            "seed_sensitivity": {
                "seeds": seeds.seeds,
                "mae_values": seeds.mae_values,
                "spearman_values": seeds.spearman_values,
                "mae_mean": seeds.mae_mean,
                "mae_std": seeds.mae_std,
                "spearman_mean": seeds.spearman_mean,
                "spearman_std": seeds.spearman_std,
            },
            "error_distribution": {
                "median_abs_error": errdist.median_abs_error,
                "p75_abs_error": errdist.p75_abs_error,
                "p90_abs_error": errdist.p90_abs_error,
                "p95_abs_error": errdist.p95_abs_error,
                "max_abs_error": errdist.max_abs_error,
                "bias_by_bucket": errdist.bias_by_bucket,
                "bias_by_spec": errdist.bias_by_spec,
                "bias_by_encounter": errdist.bias_by_encounter,
            },
            "calibration": [
                {
                    "bucket": row.predicted_bucket,
                    "n": row.n,
                    "mean_predicted": row.mean_predicted,
                    "mean_observed": row.mean_observed,
                    "bias": row.bias,
                }
                for row in calibration
            ],
            "ridge_diagnostic": {"lightgbm_mae": lgbm_metrics.mae, "ridge_mae": ridge_metrics.mae},
            "range_diagnostic": {
                "n": rangediag.n,
                "n_below_zero": rangediag.n_below_zero,
                "n_above_hundred": rangediag.n_above_hundred,
                "min_prediction": rangediag.min_prediction,
                "max_prediction": rangediag.max_prediction,
                "raw_mae": rangediag.raw_metrics.mae,
                "clipped_mae": rangediag.clipped_metrics.mae,
            },
            "density_current": density_current.n_groups_at_least,
            "density_planned": density_planned.n_groups_at_least,
            "coverage": {
                "current_specs": coverage.current_specs,
                "current_encounters": coverage.current_encounters,
                "current_targets": coverage.current_targets,
                "planned_specs": coverage.planned_specs,
                "planned_encounters": coverage.planned_encounters,
                "planned_targets": coverage.planned_targets,
            },
        }
        ExperimentArchitectureDecisionStore(store).save(
            run_id,
            campaign_id=args.campaign,
            dataset_fingerprint=fingerprint,
            dataset_row_count=len(dataset),
            seed=seed,
            payload=payload,
        )

        _print_summary(
            run_id,
            fingerprint,
            len(dataset),
            spec_holdout,
            encounter_holdout,
            macro_micro,
            seeds,
            errdist,
            calibration,
            rangediag,
            lgbm_metrics,
            ridge_metrics,
            paired_lgbm_vs_b0,
            paired_lgbm_vs_b1,
            paired_f2_vs_f1,
            density_current,
            density_planned,
            coverage,
        )
        return 0
    finally:
        store.close()


def _summary_to_dict(s: MacroMicroSummary) -> dict[str, object]:
    return {
        "micro_mae": s.micro.mae,
        "micro_spearman": s.micro.spearman,
        "n_groups_ok": s.n_groups_ok,
        "n_groups_total": s.n_groups_total,
        "macro_mean_mae": s.macro_mean_mae,
        "macro_median_mae": s.macro_median_mae,
        "macro_worst_mae": s.macro_worst_mae,
        "macro_p90_mae": s.macro_p90_mae,
        "macro_mean_spearman": s.macro_mean_spearman,
        "worst_group_key": s.worst_group_key,
    }


def _print_summary(
    run_id: str,
    fingerprint: str,
    n_rows: int,
    spec_holdout: Sequence[object],
    encounter_holdout: Sequence[object],
    macro_micro: dict[str, MacroMicroSummary],
    seeds: object,
    errdist: object,
    calibration: Sequence[object],
    rangediag: object,
    lgbm_metrics: object,
    ridge_metrics: object,
    paired_lgbm_vs_b0: object,
    paired_lgbm_vs_b1: object,
    paired_f2_vs_f1: object,
    density_current: object,
    density_planned: object,
    coverage: object,
) -> None:
    out = sys.stdout.write
    out(
        "STATISTICAL ARCHITECTURE DECISION GATE (offline, zero chamadas WCL)\n\n"
        f"  run_id ............................... {run_id}\n"
        f"  dataset_fingerprint .................. {fingerprint}\n"
        f"  dataset_row_count .................... {n_rows}\n"
        f"  leave-one-spec-out folds ............. {len(spec_holdout)}\n"
        f"  leave-one-encounter-out folds ........ {len(encounter_holdout)}\n\n"
    )
    out("  macro/micro (MODEL_GLOBAL, lightgbm, f2_full_covariates)\n")
    for dim in ("spec", "encounter"):
        s = macro_micro[f"{dim}/f2_full_covariates/lightgbm"]
        macro_mean = s.macro_mean_mae or 0
        macro_worst = s.macro_worst_mae or 0
        out(
            f"    {dim:<10} micro_MAE={s.micro.mae:6.2f} macro_mean_MAE={macro_mean:6.2f} "
            f"macro_worst_MAE={macro_worst:6.2f} (worst={s.worst_group_key}) "
            f"n_ok={s.n_groups_ok}/{s.n_groups_total}\n"
        )
    out(
        "\n  seed sensitivity (LightGBM, S1, F2)\n"
        f"    {seeds}\n\n"  # type: ignore[str-format]
        "  error distribution (S1, MODEL_GLOBAL, F2, LightGBM)\n"
        f"    {errdist}\n\n"  # type: ignore[str-format]
    )
    out("  calibration by predicted bucket (S1, MODEL_GLOBAL, F2, LightGBM)\n")
    for row in calibration:
        out(f"    {row}\n")  # type: ignore[str-format]
    out(f"\n  range diagnostic (S1, F2, LightGBM): {rangediag}\n")  # type: ignore[str-format]
    out(
        "\n  ridge diagnostic (S1, F2): lightgbm vs ridge\n"
        f"    lightgbm_mae={lgbm_metrics.mae:.2f} ridge_mae={ridge_metrics.mae:.2f}\n\n"  # type: ignore[attr-defined]
        "  paired bootstrap (S1, F2, delta = a - b)\n"
        f"    lightgbm vs baseline_0: {paired_lgbm_vs_b0}\n"
        f"    lightgbm vs baseline_1: {paired_lgbm_vs_b1}\n"
        f"    f2 vs f1 (lightgbm):    {paired_f2_vs_f1}\n\n"
        f"  density (current 603): {density_current}\n"  # type: ignore[str-format]
        f"  density (planned 1200): {density_planned}\n"  # type: ignore[str-format]
        f"  coverage: {coverage}\n\n"  # type: ignore[str-format]
        "Nenhum modelo foi promovido a phase4_model_registry.\n"
        "API points consumidos ............... 0\n"
    )
