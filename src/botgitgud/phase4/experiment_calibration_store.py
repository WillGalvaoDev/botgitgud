"""Persistence for the Global Model Validation & Calibration Gate.

A separate table from `phase4_model_registry`, `experiment_architecture_
eval_runs` (SAE.8) and `experiment_architecture_decision_runs` (SAD.1) —
this artifact is a calibration study, never a production model or
calibrator. Nothing here can ever be consumed by `!analisar`: only this
module and its own tests read this table.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from botgitgud.ingest.store import Store

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS experiment_calibration_runs (
    run_id VARCHAR PRIMARY KEY,
    campaign_id VARCHAR NOT NULL,
    dataset_fingerprint VARCHAR NOT NULL,
    dataset_row_count INTEGER NOT NULL,
    seed INTEGER NOT NULL,
    n_train INTEGER NOT NULL,
    n_calibration INTEGER NOT NULL,
    n_validation INTEGER NOT NULL,
    payload_json VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL
)
"""


class ExperimentCalibrationStore:
    def __init__(self, store: Store) -> None:
        self._store = store
        store.execute(_CREATE_TABLE)

    def save(
        self,
        run_id: str,
        *,
        campaign_id: str,
        dataset_fingerprint: str,
        dataset_row_count: int,
        seed: int,
        n_train: int,
        n_calibration: int,
        n_validation: int,
        payload: dict[str, Any],
    ) -> None:
        self._store.execute(
            "INSERT OR REPLACE INTO experiment_calibration_runs "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                run_id,
                campaign_id,
                dataset_fingerprint,
                dataset_row_count,
                seed,
                n_train,
                n_calibration,
                n_validation,
                json.dumps(payload),
                datetime.now(UTC),
            ],
        )

    def list_runs(self, campaign_id: str) -> list[dict[str, Any]]:
        rows = self._store.execute_returning(
            "SELECT run_id, dataset_fingerprint, dataset_row_count, seed, "
            "n_train, n_calibration, n_validation, created_at "
            "FROM experiment_calibration_runs "
            "WHERE campaign_id = ? ORDER BY created_at DESC",
            [campaign_id],
        )
        return [
            {
                "run_id": r[0],
                "dataset_fingerprint": r[1],
                "dataset_row_count": r[2],
                "seed": r[3],
                "n_train": r[4],
                "n_calibration": r[5],
                "n_validation": r[6],
                "created_at": r[7],
            }
            for r in rows
        ]
