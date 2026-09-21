"""Persistence for the Statistical Architecture Decision Gate.

A separate table from both `phase4_model_registry` and SAE.8's
`experiment_architecture_eval_runs` — this artifact's shape (per-fold
leave-one-out results, seed sensitivity, calibration) doesn't fit the
(granularity x split x family x model) cell abstraction the SAE.8 table
stores. Like SAE.8's store, this can never make a model `READY`: only raw
MEASURED numbers are persisted here, never a decision — the architecture
decision itself lives in the markdown report, reasoned by a human from
these numbers, not recomputed mechanically from this table.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from botgitgud.ingest.store import Store
from botgitgud.phase4.experiment_decision import GroupHoldoutResult

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS experiment_architecture_decision_runs (
    run_id VARCHAR PRIMARY KEY,
    campaign_id VARCHAR NOT NULL,
    dataset_fingerprint VARCHAR NOT NULL,
    dataset_row_count INTEGER NOT NULL,
    seed INTEGER NOT NULL,
    payload_json VARCHAR NOT NULL,
    created_at TIMESTAMP NOT NULL
)
"""


def holdout_results_to_json(results: list[GroupHoldoutResult]) -> list[dict[str, Any]]:
    rows = []
    for r in results:
        by_family: dict[str, Any] = {}
        for family, fp in r.by_family.items():
            by_family[family.value] = {
                "y_true": list(fp.y_true),
                "predictions": {model: list(preds) for model, preds in fp.predictions.items()},
            }
        rows.append(
            {
                "key": r.key,
                "n_train": r.n_train,
                "n_validation": r.n_validation,
                "status": r.status.value,
                "by_family": by_family,
            }
        )
    return rows


class ExperimentArchitectureDecisionStore:
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
        payload: dict[str, Any],
    ) -> None:
        self._store.execute(
            "INSERT OR REPLACE INTO experiment_architecture_decision_runs "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                run_id,
                campaign_id,
                dataset_fingerprint,
                dataset_row_count,
                seed,
                json.dumps(payload, default=_json_default),
                datetime.now(UTC),
            ],
        )

    def list_runs(self, campaign_id: str) -> list[dict[str, Any]]:
        rows = self._store.execute_returning(
            "SELECT run_id, dataset_fingerprint, dataset_row_count, seed, created_at "
            "FROM experiment_architecture_decision_runs "
            "WHERE campaign_id = ? ORDER BY created_at DESC",
            [campaign_id],
        )
        return [
            {
                "run_id": r[0],
                "dataset_fingerprint": r[1],
                "dataset_row_count": r[2],
                "seed": r[3],
                "created_at": r[4],
            }
            for r in rows
        ]


def _json_default(obj: object) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)  # type: ignore[call-overload]
    if hasattr(obj, "value"):  # StrEnum
        return obj.value  # type: ignore[union-attr]
    raise TypeError(f"not JSON serializable: {type(obj)}")
