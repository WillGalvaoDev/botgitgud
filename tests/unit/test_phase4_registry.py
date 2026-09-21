from __future__ import annotations

from pathlib import Path

import pytest

from botgitgud.domain.specs import SpecId
from botgitgud.ingest.store import Store
from botgitgud.phase4.registry import ModelStatus, Phase4ModelRecord, Phase4ModelRegistry
from botgitgud.phase4.resolver import Phase4ModelResolver, ResolutionStatus
from botgitgud.phase4.target import Phase4Target


def _target(spec: str = "Unholy") -> Phase4Target:
    return Phase4Target(SpecId("DeathKnight", spec), 3182, 5, 3)


def _ready(target: Phase4Target) -> Phase4ModelRecord:
    return Phase4ModelRecord(
        target,
        "m1",
        "d1",
        number_of_observations=5000,
        artifact_path=f"models/{target.target_id}/m1.bin",
        status=ModelStatus.READY,
    )


def test_empty_registry_resolves_unavailable(tmp_path: Path) -> None:
    store = Store(tmp_path)
    registry = Phase4ModelRegistry(store)
    assert registry.list_all() == []
    assert Phase4ModelResolver(registry).resolve(_target()).status is ResolutionStatus.UNAVAILABLE
    store.close()


def test_ready_exact_target_is_found_but_other_target_is_not(tmp_path: Path) -> None:
    store = Store(tmp_path)
    registry = Phase4ModelRegistry(store)
    registry.register(_ready(_target()))
    resolver = Phase4ModelResolver(registry)
    assert resolver.resolve(_target()).usable
    assert not resolver.resolve(_target("Frost")).usable
    assert len(registry.list_all()) == 1
    store.close()


@pytest.mark.parametrize(
    "status", [ModelStatus.INVALID, ModelStatus.COLLECTING, ModelStatus.UNAVAILABLE]
)
def test_non_ready_model_is_never_usable(tmp_path: Path, status: ModelStatus) -> None:
    store = Store(tmp_path)
    registry = Phase4ModelRegistry(store)
    registry.register(Phase4ModelRecord(_target(), status=status))
    resolution = Phase4ModelResolver(registry).resolve(_target())
    assert not resolution.usable
    assert resolution.status is (
        ResolutionStatus.INVALID if status is ModelStatus.INVALID else ResolutionStatus.UNAVAILABLE
    )
    store.close()


def test_ready_record_without_complete_metadata_is_invalid(tmp_path: Path) -> None:
    store = Store(tmp_path)
    registry = Phase4ModelRegistry(store)
    registry.register(Phase4ModelRecord(_target(), status=ModelStatus.READY))
    assert Phase4ModelResolver(registry).resolve(_target()).status is ResolutionStatus.INVALID
    store.close()


def test_corrupt_cross_target_registry_row_is_reported_incompatible(tmp_path: Path) -> None:
    store = Store(tmp_path)
    registry = Phase4ModelRegistry(store)
    registry.register(_ready(_target()))
    store.execute(
        "UPDATE phase4_model_registry SET spec_name='Frost' WHERE target_id=?",
        [_target().target_id],
    )
    resolution = Phase4ModelResolver(registry).resolve(_target())
    assert resolution.status is ResolutionStatus.INCOMPATIBLE
    assert not resolution.usable
    store.close()
