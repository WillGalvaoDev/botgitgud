"""T0.9 — supported-spec registry and scope gate (§1.4 of
docs/architecture.md): this tool analyzes DPS specs only.

Verified live against the WCL API (docs/schema_confirmado.md): both
`characterRankings`'s className/specName arguments and `playerDetails[].type`
use no-space PascalCase ("DemonHunter", "BeastMastery"), confirmed by
querying real rankings and a real report for a Demon Hunter. The allowlists
below are written with spaces for readability; `_normalize()` strips them
(and lowercases) before any comparison, so the stored form doesn't matter.

UNKNOWN is never treated as supported — failing closed is required, since
running an unrecognized spec through a `metric: dps` ranking comparison
could silently produce nonsensical advice.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import structlog

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SpecId:
    class_name: str  # as returned by the API, e.g. "DemonHunter"
    spec_name: str  # as returned by the API, e.g. "Havoc"


class SpecSupport(StrEnum):
    SUPPORTED = "supported"
    OUT_OF_SCOPE_TANK = "tank"
    OUT_OF_SCOPE_HEALER = "healer"
    OUT_OF_SCOPE_SUPPORT = "support"  # Augmentation
    UNKNOWN = "unknown"


def _normalize(name: str) -> str:
    return name.replace(" ", "").lower()


def _normalized_set(pairs: tuple[tuple[str, str], ...]) -> frozenset[tuple[str, str]]:
    return frozenset((_normalize(c), _normalize(s)) for c, s in pairs)


# 26 DPS specs — Midnight adds Demon Hunter/Devourer (reviewed 2026-09-04).
# The tool's entire scope remains codified as data, not a heuristic.
_SUPPORTED = _normalized_set(
    (
        ("Death Knight", "Frost"),
        ("Death Knight", "Unholy"),
        ("Demon Hunter", "Havoc"),
        ("Demon Hunter", "Devourer"),
        ("Druid", "Balance"),
        ("Druid", "Feral"),
        ("Evoker", "Devastation"),
        ("Hunter", "Beast Mastery"),
        ("Hunter", "Marksmanship"),
        ("Hunter", "Survival"),
        ("Mage", "Arcane"),
        ("Mage", "Fire"),
        ("Mage", "Frost"),
        ("Monk", "Windwalker"),
        ("Paladin", "Retribution"),
        ("Priest", "Shadow"),
        ("Rogue", "Assassination"),
        ("Rogue", "Outlaw"),
        ("Rogue", "Subtlety"),
        ("Shaman", "Elemental"),
        ("Shaman", "Enhancement"),
        ("Warlock", "Affliction"),
        ("Warlock", "Demonology"),
        ("Warlock", "Destruction"),
        ("Warrior", "Arms"),
        ("Warrior", "Fury"),
    )
)

_TANK_SPECS = _normalized_set(
    (
        ("Death Knight", "Blood"),
        ("Demon Hunter", "Vengeance"),
        ("Druid", "Guardian"),
        ("Monk", "Brewmaster"),
        ("Paladin", "Protection"),
        ("Warrior", "Protection"),
    )
)

_HEALER_SPECS = _normalized_set(
    (
        ("Druid", "Restoration"),
        ("Evoker", "Preservation"),
        ("Monk", "Mistweaver"),
        ("Paladin", "Holy"),
        ("Priest", "Discipline"),
        ("Priest", "Holy"),
        ("Shaman", "Restoration"),
    )
)

_SUPPORT_SPECS = _normalized_set((("Evoker", "Augmentation"),))


def classify_spec(spec: SpecId) -> SpecSupport:
    key = (_normalize(spec.class_name), _normalize(spec.spec_name))

    if key in _SUPPORTED:
        return SpecSupport.SUPPORTED
    if key in _TANK_SPECS:
        return SpecSupport.OUT_OF_SCOPE_TANK
    if key in _HEALER_SPECS:
        return SpecSupport.OUT_OF_SCOPE_HEALER
    if key in _SUPPORT_SPECS:
        return SpecSupport.OUT_OF_SCOPE_SUPPORT

    log.warning("specs.unknown_spec", class_name=spec.class_name, spec_name=spec.spec_name)
    return SpecSupport.UNKNOWN


def rejection_message(support: SpecSupport, spec: SpecId) -> str | None:
    """None for SUPPORTED; a user-facing explanation otherwise."""
    if support is SpecSupport.SUPPORTED:
        return None
    if support is SpecSupport.OUT_OF_SCOPE_TANK:
        return (
            "Análise de tanks está fora do escopo desta ferramenta. Ela avalia apenas specs de DPS."
        )
    if support is SpecSupport.OUT_OF_SCOPE_HEALER:
        return (
            "Análise de healers está fora do escopo desta ferramenta. "
            "Ela avalia apenas specs de DPS."
        )
    if support is SpecSupport.OUT_OF_SCOPE_SUPPORT:
        return (
            "Augmentation Evoker não é suportado: boa parte do seu dano é atribuída a outros "
            "jogadores, o que invalida a comparação de DPS pessoal."
        )
    return f"Spec não reconhecida: {spec.class_name}/{spec.spec_name}. Isto pode ser uma spec nova."
