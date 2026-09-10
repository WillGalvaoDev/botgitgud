"""Human-curated canonical-entity roles decided during GATE-M5 review."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from botgitgud.domain.ability_role import AbilityRole


@dataclass(frozen=True, slots=True)
class CuratedFamilyRole:
    role: AbilityRole
    provenance: str
    justification: str


_PROVENANCE = "GATE-M5-batch1-decisions.json"


def _decision(role: AbilityRole, justification: str) -> CuratedFamilyRole:
    return CuratedFamilyRole(role, _PROVENANCE, justification)


CURATED_FAMILY_ROLES = MappingProxyType(
    {
        ("Warlock", "Demonology", "Call Dreadstalkers"): _decision(
            AbilityRole.PET_SUMMON, "reviewed pet summon"
        ),
        ("Warlock", "Demonology", "Summon Demonic Tyrant"): _decision(
            AbilityRole.PET_SUMMON, "reviewed pet summon"
        ),
        ("Warlock", "Demonology", "Grimoire: Imp Lord"): _decision(
            AbilityRole.PET_SUMMON, "reviewed pet summon"
        ),
        **{
            ("Warlock", spec, name): _decision(role, f"reviewed {role.value}")
            for spec in ("Affliction", "Demonology", "Destruction")
            for name, role in (
                ("Dark Pact", AbilityRole.DEFENSIVE),
                ("Unending Resolve", AbilityRole.DEFENSIVE),
                ("Burning Rush", AbilityRole.UTILITY),
                ("Mortal Coil", AbilityRole.UTILITY),
                ("Soulburn", AbilityRole.UTILITY),
            )
        },
        ("Warlock", "Demonology", "Axe Toss"): _decision(AbilityRole.UTILITY, "reviewed utility"),
        # GATE-M5 lote 1 pos-M18: class abilities do kit de Warlock, expandidas apenas
        # pelos specs em que o corpus as observou (INV-CROLE-5 proibe criar entidade).
        **{
            ("Warlock", spec, "Demonic Circle: Teleport"): _decision(
                AbilityRole.UTILITY, "reviewed utility"
            )
            for spec in ("Affliction", "Demonology", "Destruction")
        },
        **{
            ("Warlock", spec, name): _decision(AbilityRole.UTILITY, "reviewed utility")
            for spec in ("Demonology", "Destruction")
            for name in ("Fel Domination", "Curse of Weakness")
        },
        ("Warlock", "Demonology", "Shadowfury"): _decision(AbilityRole.UTILITY, "reviewed utility"),
        ("Warlock", "Demonology", "Curse of Tongues"): _decision(
            AbilityRole.UTILITY, "reviewed utility"
        ),
        ("Warlock", "Demonology", "Create Healthstone"): _decision(
            AbilityRole.UTILITY, "reviewed utility"
        ),
        ("Warlock", "Demonology", "Create Soulwell"): _decision(
            AbilityRole.UTILITY, "reviewed utility"
        ),
        ("Warlock", "Demonology", "Summon Felguard"): _decision(
            AbilityRole.PET_SUMMON, "reviewed pet summon"
        ),
        # GATE-M5 lote 2 pos-M18 — Warrior/Arms.  Avatar e Sweeping Strikes estavam
        # entre os 233 que M17 removeu de OFFENSIVE_COOLDOWN por falta de corroboracao;
        # voltam aqui por DECISAO CURADA, nunca por cadencia, que e o que INV-CROLE-8
        # permite explicitamente.
        ("Warrior", "Arms", "Avatar"): _decision(
            AbilityRole.OFFENSIVE_COOLDOWN, "reviewed offensive cooldown"
        ),
        ("Warrior", "Arms", "Sweeping Strikes"): _decision(
            AbilityRole.SELF_OFFENSIVE_BUFF, "reviewed self offensive buff"
        ),
        ("Warrior", "Arms", "Die by the Sword"): _decision(
            AbilityRole.DEFENSIVE, "reviewed defensive"
        ),
        ("Warrior", "Arms", "Intimidating Shout"): _decision(
            AbilityRole.UTILITY, "reviewed utility"
        ),
        ("Warrior", "Arms", "Berserker Rage"): _decision(AbilityRole.UTILITY, "reviewed utility"),
        ("Warrior", "Arms", "Taunt"): _decision(AbilityRole.UTILITY, "reviewed utility"),
        ("Warrior", "Arms", "Shattering Throw"): _decision(AbilityRole.UTILITY, "reviewed utility"),
        ("Warrior", "Arms", "Intervene"): _decision(AbilityRole.UTILITY, "reviewed utility"),
        **{
            ("Warrior", spec, "Pummel"): _decision(AbilityRole.UTILITY, "reviewed utility")
            for spec in ("Arms", "Fury")
        },
        **{
            ("Warrior", spec, "Defensive Stance"): _decision(
                AbilityRole.DEFENSIVE, "reviewed defensive"
            )
            for spec in ("Arms", "Fury")
        },
        # GATE-M5 lote 3 — Druid/Balance.  As cinco abilities observadas tambem em Feral
        # sao expandidas para os dois specs: mesma ability, mesma classe, mesmo papel.
        **{
            ("Druid", spec, name): _decision(role, f"reviewed {role.value}")
            for spec in ("Balance", "Feral")
            for name, role in (
                ("Barkskin", AbilityRole.DEFENSIVE),
                ("Convoke the Spirits", AbilityRole.OFFENSIVE_COOLDOWN),
                ("Dash", AbilityRole.UTILITY),
                ("Innervate", AbilityRole.UTILITY),
                ("Wild Charge", AbilityRole.UTILITY),
            )
        },
        **{
            ("Druid", "Balance", name): _decision(role, f"reviewed {role.value}")
            for name, role in (
                ("Incarnation: Chosen of Elune", AbilityRole.OFFENSIVE_COOLDOWN),
                ("Celestial Alignment", AbilityRole.OFFENSIVE_COOLDOWN),
                ("Force of Nature", AbilityRole.PET_SUMMON),
                ("Solar Eclipse", AbilityRole.SELF_OFFENSIVE_BUFF),
                ("Lunar Eclipse", AbilityRole.SELF_OFFENSIVE_BUFF),
                ("Atmospheric Exposure", AbilityRole.SELF_OFFENSIVE_BUFF),
                ("Solar Beam", AbilityRole.UTILITY),
                ("Rebirth", AbilityRole.UTILITY),
                ("Typhoon", AbilityRole.UTILITY),
                ("Ursol's Vortex", AbilityRole.UTILITY),
            )
        },
        # GATE-M5 lote 4 — cross-spec.  Uma decisao humana por NOME, expandida
        # apenas pelos (class, spec) em que o corpus a observou.
        ("Hunter", "BeastMastery", "Aspect of the Turtle"): _decision(
            AbilityRole.DEFENSIVE, "reviewed defensive"
        ),
        ("Hunter", "Marksmanship", "Aspect of the Turtle"): _decision(
            AbilityRole.DEFENSIVE, "reviewed defensive"
        ),
        ("Hunter", "Survival", "Aspect of the Turtle"): _decision(
            AbilityRole.DEFENSIVE, "reviewed defensive"
        ),
        ("Shaman", "Elemental", "Astral Shift"): _decision(
            AbilityRole.DEFENSIVE, "reviewed defensive"
        ),
        ("Shaman", "Enhancement", "Astral Shift"): _decision(
            AbilityRole.DEFENSIVE, "reviewed defensive"
        ),
        ("Rogue", "Assassination", "Cloak of Shadows"): _decision(
            AbilityRole.DEFENSIVE, "reviewed defensive"
        ),
        ("Rogue", "Outlaw", "Cloak of Shadows"): _decision(
            AbilityRole.DEFENSIVE, "reviewed defensive"
        ),
        ("Rogue", "Subtlety", "Cloak of Shadows"): _decision(
            AbilityRole.DEFENSIVE, "reviewed defensive"
        ),
        ("Mage", "Arcane", "Counterspell"): _decision(AbilityRole.UTILITY, "reviewed utility"),
        ("Mage", "Fire", "Counterspell"): _decision(AbilityRole.UTILITY, "reviewed utility"),
        ("Mage", "Frost", "Counterspell"): _decision(AbilityRole.UTILITY, "reviewed utility"),
        ("Rogue", "Outlaw", "Coup de Grace"): _decision(
            AbilityRole.RESOURCE_SPENDER, "reviewed resource_spender"
        ),
        ("Rogue", "Subtlety", "Coup de Grace"): _decision(
            AbilityRole.RESOURCE_SPENDER, "reviewed resource_spender"
        ),
        ("Rogue", "Assassination", "Crimson Vial"): _decision(
            AbilityRole.HEALING, "reviewed healing"
        ),
        ("Rogue", "Outlaw", "Crimson Vial"): _decision(AbilityRole.HEALING, "reviewed healing"),
        ("Rogue", "Subtlety", "Crimson Vial"): _decision(AbilityRole.HEALING, "reviewed healing"),
        ("DeathKnight", "Frost", "Death's Advance"): _decision(
            AbilityRole.UTILITY, "reviewed utility"
        ),
        ("DeathKnight", "Unholy", "Death's Advance"): _decision(
            AbilityRole.UTILITY, "reviewed utility"
        ),
        ("Hunter", "BeastMastery", "Disengage"): _decision(AbilityRole.UTILITY, "reviewed utility"),
        ("Hunter", "Marksmanship", "Disengage"): _decision(AbilityRole.UTILITY, "reviewed utility"),
        ("Hunter", "Survival", "Disengage"): _decision(AbilityRole.UTILITY, "reviewed utility"),
        ("Rogue", "Assassination", "Evasion"): _decision(
            AbilityRole.DEFENSIVE, "reviewed defensive"
        ),
        ("Rogue", "Outlaw", "Evasion"): _decision(AbilityRole.DEFENSIVE, "reviewed defensive"),
        ("Rogue", "Subtlety", "Evasion"): _decision(AbilityRole.DEFENSIVE, "reviewed defensive"),
        ("Hunter", "BeastMastery", "Exhilaration"): _decision(
            AbilityRole.HEALING, "reviewed healing"
        ),
        ("Hunter", "Marksmanship", "Exhilaration"): _decision(
            AbilityRole.HEALING, "reviewed healing"
        ),
        ("Hunter", "Survival", "Exhilaration"): _decision(AbilityRole.HEALING, "reviewed healing"),
        ("Rogue", "Assassination", "Feint"): _decision(AbilityRole.DEFENSIVE, "reviewed defensive"),
        ("Rogue", "Outlaw", "Feint"): _decision(AbilityRole.DEFENSIVE, "reviewed defensive"),
        ("Rogue", "Subtlety", "Feint"): _decision(AbilityRole.DEFENSIVE, "reviewed defensive"),
        ("Shaman", "Elemental", "Gust of Wind"): _decision(AbilityRole.UTILITY, "reviewed utility"),
        ("Shaman", "Enhancement", "Gust of Wind"): _decision(
            AbilityRole.UTILITY, "reviewed utility"
        ),
        ("Hunter", "BeastMastery", "Hunter's Mark"): _decision(
            AbilityRole.SELF_OFFENSIVE_BUFF, "reviewed self_offensive_buff"
        ),
        ("Hunter", "Marksmanship", "Hunter's Mark"): _decision(
            AbilityRole.SELF_OFFENSIVE_BUFF, "reviewed self_offensive_buff"
        ),
        ("Hunter", "Survival", "Hunter's Mark"): _decision(
            AbilityRole.SELF_OFFENSIVE_BUFF, "reviewed self_offensive_buff"
        ),
        ("Mage", "Arcane", "Ice Cold"): _decision(AbilityRole.DEFENSIVE, "reviewed defensive"),
        ("Mage", "Fire", "Ice Cold"): _decision(AbilityRole.DEFENSIVE, "reviewed defensive"),
        ("Mage", "Frost", "Ice Cold"): _decision(AbilityRole.DEFENSIVE, "reviewed defensive"),
        ("Hunter", "BeastMastery", "Intimidation"): _decision(
            AbilityRole.UTILITY, "reviewed utility"
        ),
        ("Hunter", "Marksmanship", "Intimidation"): _decision(
            AbilityRole.UTILITY, "reviewed utility"
        ),
        ("Hunter", "Survival", "Intimidation"): _decision(AbilityRole.UTILITY, "reviewed utility"),
        ("Rogue", "Assassination", "Kick"): _decision(AbilityRole.UTILITY, "reviewed utility"),
        ("Rogue", "Outlaw", "Kick"): _decision(AbilityRole.UTILITY, "reviewed utility"),
        ("Rogue", "Subtlety", "Kick"): _decision(AbilityRole.UTILITY, "reviewed utility"),
        ("Rogue", "Assassination", "Shadowstep"): _decision(
            AbilityRole.UTILITY, "reviewed utility"
        ),
        ("Rogue", "Subtlety", "Shadowstep"): _decision(AbilityRole.UTILITY, "reviewed utility"),
        ("Rogue", "Assassination", "Sprint"): _decision(AbilityRole.UTILITY, "reviewed utility"),
        ("Rogue", "Outlaw", "Sprint"): _decision(AbilityRole.UTILITY, "reviewed utility"),
        ("Rogue", "Subtlety", "Sprint"): _decision(AbilityRole.UTILITY, "reviewed utility"),
        ("Hunter", "BeastMastery", "Survival of the Fittest"): _decision(
            AbilityRole.DEFENSIVE, "reviewed defensive"
        ),
        ("Hunter", "Marksmanship", "Survival of the Fittest"): _decision(
            AbilityRole.DEFENSIVE, "reviewed defensive"
        ),
        ("Hunter", "Survival", "Survival of the Fittest"): _decision(
            AbilityRole.DEFENSIVE, "reviewed defensive"
        ),
    }
)
