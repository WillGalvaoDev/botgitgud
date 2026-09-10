from __future__ import annotations

from dataclasses import replace
from itertools import permutations

import pytest
from real_corpus import require_real_corpus

from botgitgud.analysis.ability_classification import classify_abilities
from botgitgud.analysis.canonical_role import (
    derive_canonical_roles,
    index_canonical_roles_for_spec,
)
from botgitgud.domain.ability_identity import AbilityIdentity, IdentitySource
from botgitgud.domain.ability_role import AbilityRole
from botgitgud.domain.canonical_ability import CanonicalAbility, FamilyProvenance
from botgitgud.domain.canonical_role import CanonicalAbilityRole, RoleSource
from botgitgud.domain.curated_family_roles import CuratedFamilyRole
from botgitgud.domain.models import AbilityDamage, FightRef, PlayerBuild, PlayerLog


def _family(
    name: str = "Spell",
    *,
    spec: str = "Demonology",
    cast_ids: tuple[int, ...] = (1,),
    damage_ids: tuple[int, ...] = (2,),
) -> CanonicalAbility:
    return CanonicalAbility(
        "Warlock", spec, name, cast_ids, damage_ids, FamilyProvenance.DERIVED_NAME_MATCH, False
    )


def _log(damage: dict[int, float], *, spec: str = "Demonology", partition: int = 3) -> PlayerLog:
    return PlayerLog(
        FightRef("r", 1, 1, "Boss", 5, 100.0, True, partition=partition),
        PlayerBuild("P", None, "Warlock", spec, "dps", None, None, None),
        None,
        None,
        {},
        damage_by_ability={key: AbilityDamage(key, value, 1, 0) for key, value in damage.items()},
    )


@pytest.mark.parametrize(
    "values",
    [
        (AbilityRole.CORE_DAMAGE, RoleSource.UNRESOLVED, ()),
        (None, RoleSource.DERIVED, ("share",)),
        (None, RoleSource.UNRESOLVED, ("reason",)),
        (AbilityRole.CORE_DAMAGE, RoleSource.DERIVED, ()),
    ],
)
def test_structural_invariant_rejects_every_inconsistent_combination(
    values: tuple[AbilityRole | None, RoleSource, tuple[str, ...]],
) -> None:
    with pytest.raises(ValueError):
        CanonicalAbilityRole("Warlock", "Demo", "Spell", *values, (1,))


def test_empty_canonical_name_is_rejected() -> None:
    with pytest.raises(ValueError):
        CanonicalAbilityRole("Warlock", "Demo", "", None, RoleSource.UNRESOLVED, (), (1,))


@pytest.mark.parametrize("member_ids", [(), (2, 1), (1, 1)])
def test_member_ids_must_be_nonempty_sorted_and_unique(member_ids: tuple[int, ...]) -> None:
    with pytest.raises(ValueError):
        CanonicalAbilityRole(
            "Warlock", "Demo", "Spell", None, RoleSource.UNRESOLVED, (), member_ids
        )


def test_derivation_aggregates_family_damage_and_uses_existing_threshold() -> None:
    family = _family(damage_ids=(2, 3))
    role = derive_canonical_roles((_log({2: 2.0, 3: 2.0, 9: 96.0}),), (family,))[0]
    assert role.role is AbilityRole.CORE_DAMAGE
    assert role.role_source is RoleSource.DERIVED
    assert role.evidence == ("family_damage_share=4.00%",)
    assert role.member_ids == (1, 2, 3)


def test_below_threshold_is_secondary_and_missing_damage_is_unresolved() -> None:
    families = (_family("Low"), _family("Missing", cast_ids=(3,), damage_ids=(4,)))
    roles = derive_canonical_roles((_log({2: 2.0, 9: 98.0}),), families)
    assert roles[0].role is AbilityRole.SECONDARY_DAMAGE
    assert roles[1].role is None
    assert roles[1].role_source is RoleSource.UNRESOLVED


def test_curated_wins_over_derivation_and_unknown_curated_key_creates_nothing() -> None:
    family = _family()
    curated = {
        ("Warlock", "Demonology", "Spell"): CuratedFamilyRole(
            AbilityRole.UTILITY, "source", "human decision"
        ),
        ("Mage", "Arcane", "Absent"): CuratedFamilyRole(
            AbilityRole.DEFENSIVE, "source", "must be ignored"
        ),
    }
    roles = derive_canonical_roles((_log({2: 100.0}),), (family,), curated=curated)
    assert len(roles) == 1
    assert roles[0].role is AbilityRole.UTILITY
    assert roles[0].role_source is RoleSource.CURATED


def test_observed_curated_entity_without_family_is_emitted_and_projected() -> None:
    log = _log({9: 100.0})
    log = replace(log, cast_timeline={1: (10.0,)})
    identities = {1: AbilityIdentity(1, "Dark Pact", IdentitySource.WCL_TABLE, "resolved")}
    curated = {
        ("Warlock", "Demonology", "Dark Pact"): CuratedFamilyRole(
            AbilityRole.DEFENSIVE, "source", "human decision"
        ),
        ("Warlock", "Demonology", "Absent"): CuratedFamilyRole(
            AbilityRole.UTILITY, "source", "must not create an entity"
        ),
    }

    roles = derive_canonical_roles((log,), (), identities=identities, curated=curated)

    assert len(roles) == 1
    assert roles[0].canonical_name == "Dark Pact"
    assert roles[0].member_ids == (1,)
    assert roles[0].role is AbilityRole.DEFENSIVE
    assert index_canonical_roles_for_spec(roles, "Warlock", "Demonology") == {1: roles[0]}


def test_permuting_logs_does_not_change_semantics() -> None:
    logs = (_log({2: 1.0, 3: 2.0, 9: 47.0}), _log({2: 2.0, 3: 1.0, 9: 47.0}))
    first = _family(damage_ids=(2, 3))
    expected = derive_canonical_roles(logs, (first,))[0]
    assert all(
        derive_canonical_roles(order, (first,))[0] == expected for order in permutations(logs)
    )


def test_spec_scoped_index_projects_every_member_without_cross_spec_collision() -> None:
    roles = (
        CanonicalAbilityRole(
            "Warlock",
            "Demonology",
            "Ruination",
            AbilityRole.CORE_DAMAGE,
            RoleSource.DERIVED,
            ("share",),
            (1, 42),
        ),
        CanonicalAbilityRole(
            "Warlock",
            "Destruction",
            "Ruination",
            AbilityRole.SECONDARY_DAMAGE,
            RoleSource.DERIVED,
            ("share",),
            (1, 42),
        ),
    )
    demo = index_canonical_roles_for_spec(roles, "Warlock", "Demonology")
    destro = index_canonical_roles_for_spec(roles, "Warlock", "Destruction")
    assert set(demo) == {1, 42}
    assert all(role.role is AbilityRole.CORE_DAMAGE for role in demo.values())
    assert all(role.role is AbilityRole.SECONDARY_DAMAGE for role in destro.values())


def test_spec_scoped_index_removes_both_entities_that_claim_one_member() -> None:
    first = CanonicalAbilityRole(
        "Warlock",
        "Demonology",
        "First",
        AbilityRole.UTILITY,
        RoleSource.CURATED,
        ("reviewed",),
        (1, 2),
    )
    second = CanonicalAbilityRole(
        "Warlock",
        "Demonology",
        "Second",
        AbilityRole.DEFENSIVE,
        RoleSource.CURATED,
        ("reviewed",),
        (2, 3),
    )

    assert index_canonical_roles_for_spec((first, second), "Warlock", "Demonology") == {}


def test_curated_entities_have_effect_on_a_real_player_log() -> None:
    expected = {
        104316: ("Call Dreadstalkers", AbilityRole.PET_SUMMON),
        108416: ("Dark Pact", AbilityRole.DEFENSIVE),
        119914: ("Axe Toss", AbilityRole.UTILITY),
    }
    player_log = next(
        log
        for log in require_real_corpus()
        if log.build.class_name == "Warlock"
        and log.build.spec_name == "Demonology"
        and set(expected)
        <= (set(log.cast_timeline) | set(log.damage_by_ability) | set(log.uptimes))
    )
    identities = {
        spell_id: AbilityIdentity(spell_id, name, IdentitySource.WCL_TABLE, "resolved")
        for spell_id, (name, _role) in expected.items()
    }

    roles = derive_canonical_roles((player_log,), (), identities=identities)
    role_index = index_canonical_roles_for_spec(roles, "Warlock", "Demonology")
    classifications = classify_abilities(player_log, canonical_roles=role_index)

    assert {role.role_source for role in roles} == {RoleSource.CURATED}
    for spell_id, (_name, expected_role) in expected.items():
        assert classifications[spell_id].role is expected_role
        assert classifications[spell_id].rule == "rule_canonical_role:curated"
