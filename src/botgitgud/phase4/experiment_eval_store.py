"""Persistence for statistical-architecture evaluation runs.

Deliberately a separate table from `phase4_model_registry` (registry.py):
these are exploratory artifacts from an experimental campaign, never a
production model capability. `Phase4ModelResolver` never reads this table,
and nothing here ever writes to `phase4_model_registry` — an evaluation run
can never become `READY` by construction, not just by convention.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from botgitgud.ingest.store import Store
from botgitgud.phase4.experiment_classify import ClassificationResult
from botgitgud.phase4.experiment_evaluate import CellResult, MatrixResult

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS experiment_architecture_eval_runs (
    run_id VARCHAR PRIMARY KEY,
    campaign_id VARCHAR NOT NULL,
    dataset_fingerprint VARCHAR NOT NULL,
    dataset_row_count INTEGER NOT NULL,
    feature_schema_version VARCHAR NOT NULL,
    seed INTEGER NOT NULL,
    min_train_rows_per_group INTEGER NOT NULL,
    min_validation_rows_per_group INTEGER NOT NULL,
    bootstrap_resamples INTEGER NOT NULL,
    dependency_versions VARCHAR NOT NULL,
    classification VARCHAR NOT NULL,
    classification_justification VARCHAR NOT NULL,
    cells_json VARCHAR NOT NULL,
    sensitivity_json VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL
)
"""


def _cell_to_dict(cell: CellResult) -> dict[str, Any]:
    evaluation = None
    if cell.evaluation is not None:
        e = cell.evaluation
        evaluation = {
            "overall": asdict(e.overall),
            "by_bucket": {k: asdict(v) for k, v in e.by_bucket.items()},
            "by_spec": {k: asdict(v) for k, v in e.by_spec.items()},
            "by_encounter": {str(k): asdict(v) for k, v in e.by_encounter.items()},
            "seen_targets": asdict(e.seen_targets) if e.seen_targets is not None else None,
            "unseen_targets": asdict(e.unseen_targets) if e.unseen_targets is not None else None,
        }
    return {
        "granularity": cell.granularity.value,
        "split": cell.split.value,
        "feature_family": cell.feature_family.value,
        "model": cell.model.value,
        "status": cell.status.value,
        "reason": cell.reason,
        "evaluation": evaluation,
        "n_groups_trainable": cell.n_groups_trainable,
        "n_groups_insufficient": cell.n_groups_insufficient,
        "observations_covered": cell.observations_covered,
        "mae_ci": asdict(cell.mae_ci) if cell.mae_ci is not None else None,
        "spearman_ci": asdict(cell.spearman_ci) if cell.spearman_ci is not None else None,
        "groups": [
            {
                "key": g.key,
                "train_rows": g.train_rows,
                "validation_rows": g.validation_rows,
                "status": g.status.value,
            }
            for g in cell.groups
        ],
    }


class ExperimentArchitectureEvalStore:
    """`Store`-backed, mirroring `Phase4ModelRegistry`'s own shape."""

    def __init__(self, store: Store) -> None:
        self._store = store
        store.execute(_CREATE_TABLE)

    def save(self, run_id: str, result: MatrixResult, classification: ClassificationResult) -> None:
        sensitivity_json = json.dumps(
            {
                granularity: {str(threshold): stats for threshold, stats in by_threshold.items()}
                for granularity, by_threshold in result.sensitivity.items()
            }
        )
        self._store.execute(
            "INSERT OR REPLACE INTO experiment_architecture_eval_runs VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                run_id,
                result.config.campaign_id,
                result.dataset_fingerprint,
                result.dataset_row_count,
                result.feature_schema_version,
                result.config.seed,
                result.config.min_train_rows_per_group,
                result.config.min_validation_rows_per_group,
                result.config.bootstrap_resamples,
                json.dumps(result.dependency_versions),
                classification.classification.value,
                classification.justification,
                json.dumps([_cell_to_dict(c) for c in result.cells]),
                sensitivity_json,
                datetime.now(UTC),
            ],
        )

    def list_runs(self, campaign_id: str) -> list[dict[str, Any]]:
        rows = self._store.execute_returning(
            "SELECT run_id, dataset_fingerprint, dataset_row_count, classification, "
            "classification_justification, created_at FROM experiment_architecture_eval_runs "
            "WHERE campaign_id = ? ORDER BY created_at DESC",
            [campaign_id],
        )
        return [
            {
                "run_id": r[0],
                "dataset_fingerprint": r[1],
                "dataset_row_count": r[2],
                "classification": r[3],
                "classification_justification": r[4],
                "created_at": r[5],
            }
            for r in rows
        ]
