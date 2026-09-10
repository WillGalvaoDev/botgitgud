from __future__ import annotations

from itertools import permutations
from pathlib import Path

import pytest
from real_corpus import require_real_corpus

from botgitgud.analysis.canonical_role import derive_canonical_roles
from botgitgud.analysis.core_ability_set import (
    CoreAbilitySet,
)
from botgitgud.analysis.core_ability_set import (
    build_core_ability_set as _build_core_ability_set,
)
from botgitgud.analysis.core_ability_set import (
    build_core_ability_sets as _build_core_ability_sets,
)
from botgitgud.domain.ability_identity import AbilityIdentity, IdentitySource
from botgitgud.domain.ability_role import AbilityRole
from botgitgud.domain.canonical_ability import (
    CanonicalAbility,
    FamilyProvenance,
    build_ability_families,
    observe_specs,
)
from botgitgud.domain.canonical_role import CanonicalAbilityRole, RoleSource
from botgitgud.domain.models import AbilityDamage, AuraDetail, FightRef, PlayerBuild, PlayerLog
from botgitgud.domain.spells import SpellCatalog


def _ability(
    name: str,
    spell_id: int,
    *,
    damage_id: int | None = None,
    spec: str = "Demonology",
) -> CanonicalAbility:
    return CanonicalAbility(
        "Warlock",
        spec,
        name,
        (spell_id,),
        () if damage_id is None else (damage_id,),
        FamilyProvenance.CURATED,
        False,
    )


def _role(ability: CanonicalAbility, role: AbilityRole) -> CanonicalAbilityRole:
    return CanonicalAbilityRole(
        ability.class_name,
        ability.spec_name,
        ability.resolved_name,
        role,
        RoleSource.CURATED,
        ("test",),
        tuple(sorted(ability.cast_ids + ability.damage_ids)),
    )


def _identities(abilities: tuple[CanonicalAbility, ...]) -> dict[int, AbilityIdentity]:
    return {
        spell_id: AbilityIdentity(
            spell_id, ability.resolved_name, IdentitySource.CURATED, "resolved"
        )
        for ability in abilities
        for spell_id in ability.cast_ids + ability.damage_ids
    }


def build_core_ability_set(
    abilities: tuple[CanonicalAbility, ...],
    roles: tuple[CanonicalAbilityRole, ...],
    logs: tuple[PlayerLog, ...],
) -> CoreAbilitySet:
    return _build_core_ability_set(abilities, roles, logs, identities=_identities(abilities))


def build_core_ability_sets(
    abilities: tuple[CanonicalAbility, ...],
    roles: tuple[CanonicalAbilityRole, ...],
    logs: tuple[PlayerLog, ...],
) -> tuple[CoreAbilitySet, ...]:
    return _build_core_ability_sets(abilities, roles, logs, identities=_identities(abilities))


def _log(
    *,
    casts: dict[int, tuple[float, ...]] | None = None,
    damage: dict[int, float] | None = None,
    uptimes: dict[int, float] | None = None,
    auras: dict[int, AuraDetail] | None = None,
) -> PlayerLog:
    return PlayerLog(
        FightRef("r", 1, 1, "Boss", 5, 100.0, True),
        PlayerBuild("P", None, "Warlock", "Demonology", "dps", None, None, None),
        None,
        None,
        casts or {},
        damage_by_ability={
            spell_id: AbilityDamage(spell_id, total, 1, 0)
            for spell_id, total in (damage or {}).items()
        },
        uptimes=uptimes or {},
        aura_details=auras or {},
    )


def test_entity_members_are_ranked_once_and_damage_members_do_not_need_casts() -> None:
    family = _ability("Hand of Gul'dan", 105174, damage_id=86040)
    result = build_core_ability_set(
        (family,),
        (_role(family, AbilityRole.RESOURCE_SPENDER),),
        (_log(casts={105174: (1.0, 2.0)}, damage={86040: 80.0, 999: 20.0}),),
    )

    assert [item.ability.resolved_name for item in result.abilities] == ["Hand of Gul'dan"]
    assert result.abilities[0].evidence == ("cast",)
    assert result.accounting.core_damage == 80.0
    assert result.accounting.non_actionable_damage == 20.0


def test_tier_one_zero_damage_actions_precede_damage_and_limit_is_hard() -> None:
    actions = tuple(_ability(f"Action {index:02}", index) for index in range(1, 7))
    damage = tuple(
        _ability(f"Damage {index:02}", index, damage_id=100 + index) for index in range(7, 14)
    )
    roles = tuple(_role(item, AbilityRole.OFFENSIVE_COOLDOWN) for item in actions) + tuple(
        _role(item, AbilityRole.CORE_DAMAGE) for item in damage
    )
    log = _log(
        casts={index: (1.0,) for index in range(1, 14)},
        damage={100 + index: float(index) for index in range(7, 14)},
    )
    result = build_core_ability_set(actions + damage, roles, (log,))

    assert len(result.abilities) == 10
    assert {item.ability.resolved_name for item in result.abilities[:6]} == {
        item.resolved_name for item in actions
    }
    assert result.actionable_count == 13
    assert result.accounting.actionable_unselected_damage > 0
    assert sum(result.accounting.shares()) == pytest.approx(1.0)


def test_maintained_state_is_actionable_but_uncontrolled_indirect_and_unknown_are_not() -> None:
    buff = _ability("Lightning Shield", 1)
    indirect = _ability("Fel Firebolt", 2, damage_id=102)
    unknown = _ability("Mystery", 3, damage_id=103)
    result = build_core_ability_set(
        (buff, indirect, unknown),
        (
            _role(buff, AbilityRole.SELF_OFFENSIVE_BUFF),
            _role(indirect, AbilityRole.INDIRECT_DAMAGE),
            _role(unknown, AbilityRole.UNKNOWN),
        ),
        (_log(damage={102: 60.0, 103: 40.0}, uptimes={1: 95.0}),),
    )

    assert [item.ability.resolved_name for item in result.abilities] == ["Lightning Shield"]
    assert result.abilities[0].evidence == ("maintained_state",)
    assert result.candidate_count == 2
    assert result.actionable_count == 1
    assert result.accounting.non_actionable_damage == 100.0


def test_external_offensive_is_explicitly_ineligible() -> None:
    external = _ability("Power Infusion", 1)
    result = build_core_ability_set(
        (external,),
        (_role(external, AbilityRole.EXTERNAL_OFFENSIVE),),
        (_log(casts={1: (1.0,)}),),
    )
    assert result.candidate_count == 0
    assert result.abilities == ()


def test_log_permutations_do_not_change_selection_or_accounting() -> None:
    a = _ability("Alpha", 1, damage_id=101)
    b = _ability("Beta", 2, damage_id=102)
    roles = (_role(a, AbilityRole.CORE_DAMAGE), _role(b, AbilityRole.CORE_DAMAGE))
    logs = (
        _log(casts={1: (1.0,), 2: (2.0,)}, damage={101: 10.0}),
        _log(casts={1: (1.0,), 2: (2.0,)}, damage={102: 20.0}),
    )
    results = [build_core_ability_sets((a, b), roles, order) for order in permutations(logs)]
    assert all(result == results[0] for result in results)


def test_three_term_accounting_includes_actionable_not_selected() -> None:
    abilities = tuple(_ability(f"A{index:02}", index, damage_id=100 + index) for index in range(11))
    roles = tuple(_role(item, AbilityRole.CORE_DAMAGE) for item in abilities)
    result = build_core_ability_set(
        abilities,
        roles,
        (
            _log(
                casts={index: (1.0,) for index in range(11)},
                damage={**{100 + index: 1.0 for index in range(11)}, 999: 9.0},
            ),
        ),
    )
    assert result.accounting.core_damage == 10.0
    assert result.accounting.actionable_unselected_damage == 1.0
    assert result.accounting.non_actionable_damage == 9.0
    assert result.accounting.total_damage == 20.0
    assert result.actionable_coverage == pytest.approx(10 / 11)


def test_coverage_floor_is_eligibility_and_promotes_the_next_candidate() -> None:
    rare = _ability("Rare cooldown", 1)
    common = tuple(_ability(f"Common {index:02}", index) for index in range(2, 12))
    abilities = (rare, *common)
    roles = tuple(_role(item, AbilityRole.OFFENSIVE_COOLDOWN) for item in abilities)
    logs = tuple(
        _log(
            casts={
                **({1: (1.0,)} if index == 0 else {}),
                **{spell_id: (1.0,) for spell_id in range(2, 12)},
            }
        )
        for index in range(21)
    )

    result = build_core_ability_set(abilities, roles, logs)

    assert "Rare cooldown" not in {item.ability.resolved_name for item in result.abilities}
    assert len(result.abilities) == 10
    assert result.candidate_count == 10
    assert all(item.log_coverage >= 0.05 for item in result.abilities)


def test_resolved_entity_without_family_or_canonical_role_uses_projected_role() -> None:
    identity = AbilityIdentity(1, "Mortal Strike", IdentitySource.CURATED, "resolved")
    result = _build_core_ability_set(
        (),
        (),
        (_log(casts={1: (1.0,)}, damage={1: 100.0}),),
        identities={1: identity},
    )

    assert [item.ability.resolved_name for item in result.abilities] == ["Mortal Strike"]
    assert result.abilities[0].role is AbilityRole.CORE_DAMAGE
    assert result.accounting.core_damage == 100.0


def test_curated_zero_damage_entity_without_family_is_in_universe() -> None:
    identity = AbilityIdentity(1, "Avatar", IdentitySource.CURATED, "resolved")
    role = CanonicalAbilityRole(
        "Warlock",
        "Demonology",
        "Avatar",
        AbilityRole.OFFENSIVE_COOLDOWN,
        RoleSource.CURATED,
        ("test",),
        (1,),
    )
    result = _build_core_ability_set(
        (), (role,), (_log(casts={1: (1.0,)}),), identities={1: identity}
    )

    assert [item.ability.resolved_name for item in result.abilities] == ["Avatar"]
    assert result.abilities[0].damage == 0.0


def test_unresolved_canonical_role_is_removed_at_the_universe_boundary() -> None:
    identity = AbilityIdentity(1, "Mystery", IdentitySource.CURATED, "resolved")
    unresolved = CanonicalAbilityRole(
        "Warlock",
        "Demonology",
        "Mystery",
        None,
        RoleSource.UNRESOLVED,
        (),
        (1,),
    )

    result = _build_core_ability_set(
        (),
        (unresolved,),
        (_log(casts={1: (1.0,)}, damage={1: 100.0}),),
        identities={1: identity},
    )

    assert result.candidate_count == 0
    assert result.actionable_count == 0
    assert result.abilities == ()
    assert result.accounting.non_actionable_damage == 100.0


def test_real_corpus_acceptance_for_all_25_specs() -> None:
    logs = tuple(require_real_corpus())
    catalog = SpellCatalog(
        Path(__file__).resolve().parents[2] / "data" / "spells.json", blizzard=None
    )
    observed_ids = set().union(
        *(set(log.cast_timeline) | set(log.damage_by_ability) | set(log.uptimes) for log in logs)
    )
    identities = {spell_id: catalog.identity(spell_id) for spell_id in observed_ids}
    families = build_ability_families(observe_specs(logs), identities=identities)
    roles = derive_canonical_roles(logs, families, identities=identities)

    results = _build_core_ability_sets(families, roles, logs, identities=identities)
    assert len(results) == 25
    assert all(len(result.abilities) <= 10 for result in results)
    assert all(result.actionable_coverage >= 0.70 for result in results)
    assert all(sum(result.accounting.shares()) == pytest.approx(1.0) for result in results)
    assert all(
        item.role is not AbilityRole.UNKNOWN for result in results for item in result.abilities
    )
    assert all(item.log_coverage >= 0.05 for result in results for item in result.abilities)

    by_spec = {(result.class_name, result.spec_name): result for result in results}
    demonology = {x.ability.resolved_name for x in by_spec["Warlock", "Demonology"].abilities}
    assert {"Hand of Gul'dan", "Implosion", "Ruination"} <= demonology
    assert {"Call Dreadstalkers", "Summon Demonic Tyrant", "Grimoire: Imp Lord"} <= demonology
    assert {"Fel Firebolt", "Melee"}.isdisjoint(demonology)
    assert "Thorn Bloom" not in demonology
    arms = {x.ability.resolved_name for x in by_spec["Warrior", "Arms"].abilities}
    assert {"Avatar", "Sweeping Strikes"} <= arms
    balance = {x.ability.resolved_name for x in by_spec["Druid", "Balance"].abilities}
    assert {
        "Incarnation: Chosen of Elune",
        "Celestial Alignment",
        "Convoke the Spirits",
        "Force of Nature",
        "Solar Eclipse",
        "Lunar Eclipse",
    } <= balance
