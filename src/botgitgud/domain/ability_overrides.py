"""Partition-scoped corrections retained only when real-log evidence requires them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from botgitgud.domain.ability_role import AbilityRole


class OverrideSource(StrEnum):
    """Allowed provenance classes keep overrides auditable without editorial URLs."""

    CURATED = "curated"
    BLIZZARD = "blizzard"
    DERIVED = "derived"


@dataclass(frozen=True, slots=True)
class AbilityOverride:
    """A partition-scoped correction justified by one reviewed real log."""

    spell_id: int
    role: AbilityRole
    source: OverrideSource
    partition: int
    reviewed_at: date
    justification: str


# Deliberately empty: a correction belongs here only after discovery demonstrably fails.
ABILITY_OVERRIDES: dict[int, AbilityOverride] = {}
