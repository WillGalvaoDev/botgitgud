"""T3.1 — labels for WCL's `resourceChangeType` (docs/schema_confirmado.md
§10/§12), which mirrors WoW's own `Enum.PowerType`. Unlike spell cooldowns
(docs/architecture.md D-28, no API exposes them and the project refuses to
fabricate per-spell tuning values), power types are a small, stable,
publicly-documented Blizzard game constant that hasn't changed across
expansions — safe to curate here. Live-verified (docs/schema_confirmado.md
§12): Zarad (Demonology Warlock)'s own resourcechange events all carry
`resourceChangeType: 7`, matching `Enum.PowerType.SoulShards`.
"""

from __future__ import annotations

POWER_TYPE_LABELS: dict[int, str] = {
    0: "Mana",
    1: "Fúria (Rage)",
    2: "Foco",
    3: "Energia",
    4: "Pontos de Combo",
    5: "Runas",
    6: "Poder Rúnico",
    7: "Fragmentos de Alma",
    8: "Poder Astral",
    9: "Poder Sagrado",
    11: "Devastação (Maelstrom)",
    12: "Chi",
    13: "Insanidade",
    17: "Fúria (Fury)",
    18: "Dor (Pain)",
    19: "Essência",
}


def resource_type_label(resource_change_type: int) -> str:
    return POWER_TYPE_LABELS.get(resource_change_type, f"recurso #{resource_change_type}")
