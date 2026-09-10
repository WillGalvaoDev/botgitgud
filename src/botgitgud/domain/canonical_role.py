"""Authoritative semantic role assigned to one canonical ability entity."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from botgitgud.domain.ability_role import AbilityRole


class RoleSource(StrEnum):
    CURATED = "curated"
    DERIVED = "derived"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class CanonicalAbilityRole:
    class_name: str
    spec_name: str
    canonical_name: str
    role: AbilityRole | None
    role_source: RoleSource
    evidence: tuple[str, ...]
    member_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.canonical_name:
            raise ValueError("canonical_name must not be empty")
        if not self.member_ids:
            raise ValueError("member_ids must not be empty")
        if self.member_ids != tuple(sorted(set(self.member_ids))):
            raise ValueError("member_ids must be sorted and unique")
        unresolved = self.role_source is RoleSource.UNRESOLVED
        if unresolved != (self.role is None) or unresolved != (self.evidence == ()):
            raise ValueError("UNRESOLVED, role=None, and empty evidence must be equivalent")
