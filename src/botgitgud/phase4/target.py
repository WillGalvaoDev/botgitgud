"""Canonical, path-safe identity for an isolated Phase 4 target."""

from __future__ import annotations

from dataclasses import dataclass

from botgitgud.domain.specs import SpecId


def _canonical_part(value: str, *, field: str) -> str:
    part = value.replace(" ", "")
    if not part or not part.isascii() or not part.isalnum():
        raise ValueError(f"{field} must contain only ASCII letters and digits")
    return part


@dataclass(frozen=True, slots=True)
class Phase4Target:
    spec: SpecId
    encounter_id: int
    difficulty: int
    partition: int

    def __post_init__(self) -> None:
        class_name = _canonical_part(self.spec.class_name, field="class_name")
        spec_name = _canonical_part(self.spec.spec_name, field="spec_name")
        if self.encounter_id <= 0:
            raise ValueError("encounter_id must be positive")
        if self.difficulty <= 0:
            raise ValueError("difficulty must be positive")
        if self.partition < 0:
            raise ValueError("partition must be non-negative")
        object.__setattr__(self, "spec", SpecId(class_name=class_name, spec_name=spec_name))

    @property
    def target_id(self) -> str:
        return (
            f"{self.spec.class_name}/{self.spec.spec_name}/{self.encounter_id}/"
            f"{self.difficulty}/{self.partition}"
        )

    def to_dict(self) -> dict[str, str | int]:
        return {
            "class_name": self.spec.class_name,
            "spec_name": self.spec.spec_name,
            "encounter_id": self.encounter_id,
            "difficulty": self.difficulty,
            "partition": self.partition,
        }

    @classmethod
    def parse(cls, value: str) -> Phase4Target:
        parts = value.split("/")
        if len(parts) != 5:
            raise ValueError("target id must have five slash-separated components")
        class_name, spec_name, encounter, difficulty, partition = parts
        try:
            numeric = tuple(int(item) for item in (encounter, difficulty, partition))
        except ValueError as exc:
            raise ValueError("encounter, difficulty and partition must be integers") from exc
        return cls(SpecId(class_name, spec_name), *numeric)
