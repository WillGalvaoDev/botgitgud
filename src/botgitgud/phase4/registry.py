"""Persistent catalogue of target-isolated Phase 4 model capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import cast

from botgitgud.domain.specs import SpecId
from botgitgud.ingest.store import Store
from botgitgud.phase4.target import Phase4Target


class ModelStatus(StrEnum):
    UNAVAILABLE = "unavailable"
    COLLECTING = "collecting"
    READY = "ready"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class Phase4ModelRecord:
    target: Phase4Target
    model_version: str | None = None
    dataset_version: str | None = None
    trained_at: datetime | None = None
    train_start: datetime | None = None
    train_end: datetime | None = None
    validation_mae: float | None = None
    number_of_observations: int = 0
    artifact_path: str | None = None
    status: ModelStatus = ModelStatus.UNAVAILABLE

    def __post_init__(self) -> None:
        if self.number_of_observations < 0:
            raise ValueError("number_of_observations must be non-negative")
        if self.artifact_path is not None:
            path = PurePosixPath(self.artifact_path.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("artifact_path must be a safe relative path")


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS phase4_model_registry (
    target_id VARCHAR PRIMARY KEY,
    class_name VARCHAR NOT NULL, spec_name VARCHAR NOT NULL,
    encounter_id INTEGER NOT NULL, difficulty INTEGER NOT NULL, partition INTEGER NOT NULL,
    model_version VARCHAR, dataset_version VARCHAR,
    trained_at TIMESTAMP, train_start TIMESTAMP, train_end TIMESTAMP,
    validation_mae DOUBLE, number_of_observations INTEGER NOT NULL,
    artifact_path VARCHAR, status VARCHAR NOT NULL
)
"""


class Phase4ModelRegistry:
    def __init__(self, store: Store) -> None:
        self._store = store
        store.execute(_CREATE_TABLE)

    def register(self, record: Phase4ModelRecord) -> None:
        target = record.target
        self._store.execute(
            "INSERT OR REPLACE INTO phase4_model_registry VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                target.target_id,
                target.spec.class_name,
                target.spec.spec_name,
                target.encounter_id,
                target.difficulty,
                target.partition,
                record.model_version,
                record.dataset_version,
                record.trained_at,
                record.train_start,
                record.train_end,
                record.validation_mae,
                record.number_of_observations,
                record.artifact_path,
                record.status.value,
            ],
        )

    def get(self, target: Phase4Target) -> Phase4ModelRecord | None:
        rows = self._store.execute_returning(
            "SELECT class_name, spec_name, encounter_id, difficulty, partition, "
            "model_version, dataset_version, trained_at, train_start, train_end, "
            "validation_mae, number_of_observations, artifact_path, status "
            "FROM phase4_model_registry WHERE target_id = ?",
            [target.target_id],
        )
        return _record_from_row(rows[0]) if rows else None

    def list_all(self) -> list[Phase4ModelRecord]:
        rows = self._store.execute_returning(
            "SELECT class_name, spec_name, encounter_id, difficulty, partition, "
            "model_version, dataset_version, trained_at, train_start, train_end, "
            "validation_mae, number_of_observations, artifact_path, status "
            "FROM phase4_model_registry ORDER BY target_id"
        )
        return [_record_from_row(row) for row in rows]


def _record_from_row(row: tuple[object, ...]) -> Phase4ModelRecord:
    target = Phase4Target(
        SpecId(str(row[0]), str(row[1])),
        cast(int, row[2]),
        cast(int, row[3]),
        cast(int, row[4]),
    )
    return Phase4ModelRecord(
        target=target,
        model_version=row[5] if isinstance(row[5], str) else None,
        dataset_version=row[6] if isinstance(row[6], str) else None,
        trained_at=row[7] if isinstance(row[7], datetime) else None,
        train_start=row[8] if isinstance(row[8], datetime) else None,
        train_end=row[9] if isinstance(row[9], datetime) else None,
        validation_mae=cast(float | None, row[10]),
        number_of_observations=cast(int, row[11]),
        artifact_path=row[12] if isinstance(row[12], str) else None,
        status=ModelStatus(str(row[13])),
    )
