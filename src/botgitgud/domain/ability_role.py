"""Stable ability-role vocabulary for evidence-based classification."""

from __future__ import annotations

from enum import StrEnum

from botgitgud.domain.external_buffs import (
    EXTERNAL_NON_OFFENSIVE_IDS,
    EXTERNAL_OFFENSIVE_IDS,
)

__all__ = [
    "ACTIONABLE_ROLES",
    "EXTERNAL_NON_OFFENSIVE_IDS",
    "EXTERNAL_OFFENSIVE_IDS",
    "OFFENSIVE_ROLES",
    "AbilityRole",
]


class AbilityRole(StrEnum):
    """Stable roles spanning current evidence and later roadmap milestones."""

    CORE_DAMAGE = "core_damage"
    SECONDARY_DAMAGE = "secondary_damage"
    INDIRECT_DAMAGE = "indirect_damage"
    OFFENSIVE_COOLDOWN = "offensive_cooldown"
    EXTERNAL_OFFENSIVE = "external_offensive"
    EXTERNAL_NON_OFFENSIVE = "external_non_offensive"
    SELF_AURA_UNRESOLVED = "self_aura_unresolved"
    UNKNOWN = "unknown"
    PET_DAMAGE = "pet_damage"
    DOT_TICK = "dot_tick"
    PET_SUMMON = "pet_summon"
    RESOURCE_GENERATOR = "resource_generator"
    RESOURCE_SPENDER = "resource_spender"
    SELF_OFFENSIVE_PROC = "self_offensive_proc"
    SELF_OFFENSIVE_BUFF = "self_offensive_buff"
    DEFENSIVE = "defensive"
    UTILITY = "utility"
    HEALING = "healing"
    CONSUMABLE = "consumable"
    EQUIPMENT_EFFECT = "equipment_effect"
    RACIAL = "racial"


OFFENSIVE_ROLES: frozenset[AbilityRole] = frozenset(
    {
        AbilityRole.CORE_DAMAGE,
        AbilityRole.SECONDARY_DAMAGE,
        AbilityRole.INDIRECT_DAMAGE,
        AbilityRole.OFFENSIVE_COOLDOWN,
        AbilityRole.EXTERNAL_OFFENSIVE,
        AbilityRole.PET_DAMAGE,
        AbilityRole.DOT_TICK,
        AbilityRole.PET_SUMMON,
        AbilityRole.RESOURCE_GENERATOR,
        AbilityRole.RESOURCE_SPENDER,
        AbilityRole.SELF_OFFENSIVE_PROC,
        AbilityRole.SELF_OFFENSIVE_BUFF,
    }
)

ACTIONABLE_ROLES: frozenset[AbilityRole] = OFFENSIVE_ROLES - {
    AbilityRole.EXTERNAL_OFFENSIVE,
}
