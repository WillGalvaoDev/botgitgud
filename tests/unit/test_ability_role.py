from __future__ import annotations

from botgitgud.domain.ability_role import (
    ACTIONABLE_ROLES,
    OFFENSIVE_ROLES,
    AbilityRole,
)
from botgitgud.domain.ability_role import (
    EXTERNAL_NON_OFFENSIVE_IDS as ROLE_NON_OFFENSIVE_IDS,
)
from botgitgud.domain.ability_role import (
    EXTERNAL_OFFENSIVE_IDS as ROLE_OFFENSIVE_IDS,
)
from botgitgud.domain.external_buffs import (
    EXTERNAL_NON_OFFENSIVE_IDS,
    EXTERNAL_OFFENSIVE_IDS,
)


def test_role_sets_are_subsets_of_the_complete_vocabulary() -> None:
    assert set(AbilityRole) > OFFENSIVE_ROLES
    assert ACTIONABLE_ROLES < OFFENSIVE_ROLES


def test_external_roles_distinguish_offensive_context() -> None:
    assert AbilityRole.EXTERNAL_OFFENSIVE in OFFENSIVE_ROLES
    assert AbilityRole.EXTERNAL_OFFENSIVE not in ACTIONABLE_ROLES
    assert AbilityRole.EXTERNAL_NON_OFFENSIVE not in OFFENSIVE_ROLES
    assert AbilityRole.EXTERNAL_NON_OFFENSIVE not in ACTIONABLE_ROLES


def test_unresolved_roles_fail_closed() -> None:
    unresolved = {AbilityRole.SELF_AURA_UNRESOLVED, AbilityRole.UNKNOWN}
    assert unresolved.isdisjoint(OFFENSIVE_ROLES)
    assert unresolved.isdisjoint(ACTIONABLE_ROLES)


def test_external_buff_sets_are_reexported_as_the_same_objects() -> None:
    assert ROLE_OFFENSIVE_IDS is EXTERNAL_OFFENSIVE_IDS
    assert ROLE_NON_OFFENSIVE_IDS is EXTERNAL_NON_OFFENSIVE_IDS


def test_non_spec_roles_are_distinct_and_non_actionable() -> None:
    roles = {AbilityRole.CONSUMABLE, AbilityRole.EQUIPMENT_EFFECT, AbilityRole.RACIAL}
    assert roles.isdisjoint(OFFENSIVE_ROLES)
    assert roles.isdisjoint(ACTIONABLE_ROLES)
