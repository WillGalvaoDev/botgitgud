from __future__ import annotations

import pytest

from botgitgud.domain.specs import SpecId
from botgitgud.phase4.target import Phase4Target


def target(**changes: object) -> Phase4Target:
    values = {
        "spec": SpecId("DeathKnight", "Unholy"),
        "encounter_id": 3182,
        "difficulty": 5,
        "partition": 3,
    }
    values.update(changes)
    return Phase4Target(**values)  # type: ignore[arg-type]


def test_target_is_deterministic_serializable_and_parseable() -> None:
    assert target().target_id == "DeathKnight/Unholy/3182/5/3"
    assert Phase4Target.parse(target().target_id) == target(spec=SpecId("Death Knight", "Unholy"))
    assert target().to_dict()["encounter_id"] == 3182


@pytest.mark.parametrize(
    "change",
    [
        {"spec": SpecId("DeathKnight", "Frost")},
        {"encounter_id": 3179},
        {"difficulty": 4},
        {"partition": 4},
    ],
)
def test_different_dimensions_never_collide(change: dict[str, object]) -> None:
    assert target(**change) != target()
    assert target(**change).target_id != target().target_id


@pytest.mark.parametrize("value", ["../Unholy/3182/5/3", "Mage/Frost/x/5/3", "Mage/Frost/1/0/3"])
def test_target_rejects_unsafe_or_invalid_ids(value: str) -> None:
    with pytest.raises(ValueError):
        Phase4Target.parse(value)
