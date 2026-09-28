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


# M3.1 (docs/m3-1-specification.md D-M31-05): the exact inverse of
# POWER_TYPE_LABELS — every label here is unique (Rage and Fury share no
# spelling), so this is unambiguous.
_LABEL_TO_TYPE: dict[str, int] = {label: rtype for rtype, label in POWER_TYPE_LABELS.items()}
_LEGACY_PREFIX = "recurso #"


def resource_type_from_label(label: str) -> int | None:
    """D-M31-05: recovers the integer `resourceChangeType` identity a
    historical `PlayerLog.resource_waste` label was built from, via the
    exact inverse of POWER_TYPE_LABELS plus the `"recurso #<n>"` fallback
    `resource_type_label` itself produces for an unmapped type. Any other
    string cannot be traced back to an identity — returns `None`, which a
    caller declares LEGACY_RESOURCE_LABEL_UNMAPPABLE (D-M31-02), never a
    guessed type.

    This is IDENTITY resolution only, independent of D-M31-07's coverage
    gate: `observe_resource_waste` returns STREAM_COVERAGE_UNRECORDED for
    every historical log regardless of whether its label maps back here —
    knowing WHICH type a legacy label named never proves the historical
    paginator recorded complete coverage for it.
    """
    if label in _LABEL_TO_TYPE:
        return _LABEL_TO_TYPE[label]
    if label.startswith(_LEGACY_PREFIX):
        suffix = label[len(_LEGACY_PREFIX) :]
        if suffix.isdigit():
            return int(suffix)
    return None
