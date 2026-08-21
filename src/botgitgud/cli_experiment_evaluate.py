"""`experiment-evaluate` — runs the statistical architecture matrix
(Baseline 0/1, LightGBM x granularities A-D x splits S1-S5 x feature
families F1/F2) against whatever has already been `completed` for a
campaign (docs/fase4-statistical-architecture-results.md).

Strictly offline: this module opens `Store` directly and never constructs a
`WclClient` — unlike cli_experiment.py's other commands, "zero WCL calls"
here is a property of the code path itself, not just of never calling
`.query()`. Split into its own file (not cli_experiment.py, already at the
300-line convention's edge) per T1.6's existing pattern.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Callable

from botgitgud.config import Settings
from botgitgud.ingest.store import Store
from botgitgud.phase4.experiment import ModelGranularity, SplitProtocol
from botgitgud.phase4.experiment_classify import ClassificationResult, classify_signal
from botgitgud.phase4.experiment_eval_store import ExperimentArchitectureEvalStore
from botgitgud.phase4.experiment_evaluate import (
    ALL_FAMILIES,
    ALL_GRANULARITIES,
    ALL_MODELS,
    ALL_SPLITS,
    CellStatus,
    EvaluationConfig,
    MatrixResult,
    run_matrix,
)
from botgitgud.phase4.experiment_store import ExperimentCampaignStore
from botgitgud.phase4.experimental_dataset import ExperimentalDatasetBuilder

BuildStore = Callable[[Settings], Store]


def _default_build_store(settings: Settings) -> Store:
    return Store(settings.data_dir)


def add_experiment_evaluate_parser(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
    *,
    build_store: BuildStore = _default_build_store,
) -> None:
    p = sub.add_parser(
        "experiment-evaluate",
        help="Avalia a matriz de arquitetura estatistica (A-D x S1-S5) sobre o que ja "
        "foi coletado para uma campaign. Offline, zero chamadas WCL.",
    )
    p.add_argument("--campaign", required=True, help="campaign_id congelado.")
    p.add_argument(
        "--granularity",
        choices=[g.value for g in ALL_GRANULARITIES],
        default=None,
        help="Restringe a uma granularidade; default roda A-D.",
    )
    p.add_argument(
        "--split",
        choices=[s.value for s in ALL_SPLITS],
        default=None,
        help="Restringe a um protocolo de split; default roda S1-S5.",
    )
    p.set_defaults(func=lambda args: cmd_experiment_evaluate(args, build_store=build_store))


def cmd_experiment_evaluate(args: argparse.Namespace, *, build_store: BuildStore) -> int:
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

        granularities = (
            (ModelGranularity(args.granularity),) if args.granularity else ALL_GRANULARITIES
        )
        splits = (SplitProtocol(args.split),) if args.split else ALL_SPLITS

        config = EvaluationConfig(campaign_id=args.campaign)
        result = run_matrix(
            dataset,
            config=config,
            granularities=granularities,
            splits=splits,
            models=ALL_MODELS,
            families=ALL_FAMILIES,
        )
        classification = classify_signal(result)

        run_id = f"eval-{uuid.uuid4().hex[:16]}"
        ExperimentArchitectureEvalStore(store).save(run_id, result, classification)

        _print_result(run_id, len(stored.observations), result, classification)
        return 0
    finally:
        store.close()


def _print_result(
    run_id: str, planned: int, result: MatrixResult, classification: ClassificationResult
) -> None:
    out = sys.stdout.write
    status = "PARTIAL" if result.dataset_row_count < planned else "COMPLETE"
    out(
        "AVALIACAO DE ARQUITETURA ESTATISTICA (offline, zero chamadas WCL)\n\n"
        f"  run_id ............................... {run_id}\n"
        f"  campaign_id .......................... {result.config.campaign_id}\n"
        f"  dataset_fingerprint .................. {result.dataset_fingerprint}\n"
        f"  planned / available .................. {planned} / {result.dataset_row_count}"
        f"  (dataset_status={status})\n"
        f"  feature_schema_version ............... {result.feature_schema_version}\n"
        f"  seed .................................. {result.config.seed}\n"
        f"  min_train_rows_per_group ............. {result.config.min_train_rows_per_group}\n"
        f"  min_validation_rows_per_group ........ {result.config.min_validation_rows_per_group}\n"
        f"  bootstrap_resamples ................... {result.config.bootstrap_resamples}\n"
        f"  dependency_versions ................... {result.dependency_versions}\n\n"
    )
    evaluated = sum(1 for c in result.cells if c.status is CellStatus.EVALUATED)
    out(f"  cells evaluated ....................... {evaluated}/{len(result.cells)}\n\n")
    for c in result.cells:
        if c.status is CellStatus.EVALUATED and c.evaluation is not None:
            m = c.evaluation.overall
            rho = "n/d" if m.spearman is None else f"{m.spearman:.3f}"
            out(
                f"  {c.split.value:<28}{c.granularity.value:<16}{c.feature_family.value:<20}"
                f"{c.model.value:<12} n={m.n:<4} MAE={m.mae:6.2f} Spearman={rho}\n"
            )
        else:
            out(
                f"  {c.split.value:<28}{c.granularity.value:<16}{c.feature_family.value:<20}"
                f"{c.model.value:<12} NOT_EVALUABLE ({c.reason})\n"
            )
    out(
        f"\n  classificacao .......................... {classification.classification.value}\n"
        f"  justificativa ........................... {classification.justification}\n\n"
        "PARTIAL dataset: resultados sao PRELIMINARES, nao validacao final da Fase 4.\n"
        "Nenhum modelo foi promovido a phase4_model_registry.\n"
        "API points consumidos ............... 0\n"
    )
