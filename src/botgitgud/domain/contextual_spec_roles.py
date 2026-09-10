"""Human-reviewed ability roles keyed by exact observed spec context."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from botgitgud.domain.ability_role import AbilityRole

CONTEXTUAL_SPEC_ROLES: Mapping[tuple[str, str, int], AbilityRole] = MappingProxyType(
    {
        ("DeathKnight", "Frost", 434481): AbilityRole.EXTERNAL_OFFENSIVE,
        ("DeathKnight", "Unholy", 434481): AbilityRole.EXTERNAL_OFFENSIVE,
        ("DemonHunter", "Havoc", 434481): AbilityRole.EXTERNAL_OFFENSIVE,
        ("Druid", "Balance", 434481): AbilityRole.EXTERNAL_OFFENSIVE,
        ("Druid", "Feral", 434481): AbilityRole.EXTERNAL_OFFENSIVE,
        ("Evoker", "Devastation", 434481): AbilityRole.INDIRECT_DAMAGE,
        ("Hunter", "BeastMastery", 434481): AbilityRole.EXTERNAL_OFFENSIVE,
        ("Hunter", "Marksmanship", 434481): AbilityRole.EXTERNAL_OFFENSIVE,
        ("Hunter", "Survival", 434481): AbilityRole.EXTERNAL_OFFENSIVE,
        ("Mage", "Arcane", 434481): AbilityRole.EXTERNAL_OFFENSIVE,
        ("Mage", "Fire", 434481): AbilityRole.EXTERNAL_OFFENSIVE,
        ("Mage", "Frost", 434481): AbilityRole.EXTERNAL_OFFENSIVE,
        ("Monk", "Windwalker", 434481): AbilityRole.EXTERNAL_OFFENSIVE,
        ("Paladin", "Retribution", 434481): AbilityRole.EXTERNAL_OFFENSIVE,
        ("Priest", "Shadow", 434481): AbilityRole.EXTERNAL_OFFENSIVE,
        ("Rogue", "Assassination", 434481): AbilityRole.EXTERNAL_OFFENSIVE,
        ("Rogue", "Outlaw", 434481): AbilityRole.EXTERNAL_OFFENSIVE,
        ("Rogue", "Subtlety", 434481): AbilityRole.EXTERNAL_OFFENSIVE,
        ("Shaman", "Elemental", 434481): AbilityRole.EXTERNAL_OFFENSIVE,
        ("Shaman", "Enhancement", 434481): AbilityRole.EXTERNAL_OFFENSIVE,
        ("Warlock", "Demonology", 434481): AbilityRole.EXTERNAL_OFFENSIVE,
        ("Warlock", "Destruction", 434481): AbilityRole.EXTERNAL_OFFENSIVE,
        ("Warrior", "Arms", 434481): AbilityRole.EXTERNAL_OFFENSIVE,
        ("Warrior", "Fury", 434481): AbilityRole.EXTERNAL_OFFENSIVE,
    }
)
