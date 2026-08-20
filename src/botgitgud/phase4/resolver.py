"""Exact-only capability resolution for future Phase 4 inference."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from botgitgud.phase4.registry import ModelStatus, Phase4ModelRecord, Phase4ModelRegistry
from botgitgud.phase4.target import Phase4Target


class ResolutionStatus(StrEnum):
    FOUND = "found"
    UNAVAILABLE = "unavailable"
    INCOMPATIBLE = "incompatible"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class ModelResolution:
    status: ResolutionStatus
    record: Phase4ModelRecord | None = None

    @property
    def usable(self) -> bool:
        return self.status is ResolutionStatus.FOUND


class Phase4ModelResolver:
    def __init__(self, registry: Phase4ModelRegistry) -> None:
        self._registry = registry

    def resolve(self, target: Phase4Target) -> ModelResolution:
        record = self._registry.get(target)
        if record is None:
            return ModelResolution(ResolutionStatus.UNAVAILABLE)
        if record.target != target:
            return ModelResolution(ResolutionStatus.INCOMPATIBLE, record)
        if record.status is ModelStatus.INVALID:
            return ModelResolution(ResolutionStatus.INVALID, record)
        if record.status is not ModelStatus.READY:
            return ModelResolution(ResolutionStatus.UNAVAILABLE, record)
        if not record.model_version or not record.dataset_version or not record.artifact_path:
            return ModelResolution(ResolutionStatus.INVALID, record)
        return ModelResolution(ResolutionStatus.FOUND, record)
