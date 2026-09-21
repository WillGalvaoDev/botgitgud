from __future__ import annotations

from itertools import permutations

import pytest

from botgitgud.domain.ability_identity import AbilityIdentity, IdentitySource
from botgitgud.domain.canonical_ability import (
    CanonicalAbility,
    FamilyProvenance,
    SpecObservation,
    build_ability_families,
    index_families_for_spec,
)


def _identity(spell_id: int, name: str) -> AbilityIdentity:
    return AbilityIdentity(spell_id, name, IdentitySource.CURATED, "resolved")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"resolved_name": ""},
        {"cast_ids": (2, 1)},
        {"cast_ids": (1, 1)},
        {"damage_ids": (3, 2)},
        {"damage_ids": (2, 2)},
        {"cast_ids": (1,), "damage_ids": (1,)},
        {"cast_ids": (), "damage_ids": ()},
        {"cast_ids": (1, 2), "multi_cast": False},
    ],
)
def test_structural_invariants(kwargs: dict[str, object]) -> None:
    values: dict[str, object] = {
        "class_name": "Warlock",
        "spec_name": "Demonology",
        "resolved_name": "Spell",
        "cast_ids": (1,),
        "damage_ids": (2,),
        "provenance": FamilyProvenance.DERIVED_NAME_MATCH,
        "multi_cast": False,
    }
    values.update(kwargs)
    with pytest.raises(ValueError):
        CanonicalAbility(**values)  # type: ignore[arg-type]


def test_exact_resolved_name_and_spec_scope_support_one_to_many_and_many_to_many() -> None:
    observations = (
        SpecObservation("Warrior", "Arms", frozenset({281000}), frozenset({260798})),
        SpecObservation("Warrior", "Fury", frozenset({5308, 280735}), frozenset({280849})),
        SpecObservation("DeathKnight", "Frost", frozenset({49020}), frozenset({2, 3, 4, 5, 6})),
    )
    identities = {
        sid: _identity(sid, name)
        for sid, name in {
            281000: "Execute",
            260798: "Execute",
            5308: "Execute",
            280735: "Execute",
            280849: "Execute",
            49020: "Obliterate",
            2: "Obliterate",
            3: "Obliterate",
            4: "Obliterate",
            5: "Obliterate",
            6: "Obliterate",
        }.items()
    }
    families = build_ability_families(observations, identities=identities)
    assert [(f.spec_name, f.cast_ids, f.damage_ids, f.multi_cast) for f in families] == [
        ("Frost", (49020,), (2, 3, 4, 5, 6), False),
        ("Arms", (281000,), (260798,), False),
        ("Fury", (5308, 280735), (280849,), True),
    ]


def test_unresolved_overlap_missing_side_and_different_names_fail_closed() -> None:
    observations = (SpecObservation("Warlock", "Demo", frozenset({1, 3, 5}), frozenset({2, 3, 6})),)
    identities = {
        1: _identity(1, "Call Dreadstalkers"),
        2: _identity(2, "Dreadbite"),
        3: _identity(3, "Shadow Bolt"),
        5: AbilityIdentity(5, "", IdentitySource.UNRESOLVED, "unresolved"),
        6: _identity(6, "Unresolved Pair"),
    }
    assert build_ability_families(observations, identities=identities) == ()


def test_input_permutation_does_not_change_families() -> None:
    observations = (
        SpecObservation("Warlock", "Demo", frozenset({1}), frozenset({2})),
        SpecObservation("Mage", "Fire", frozenset({3}), frozenset({4})),
    )
    identities = {
        1: _identity(1, "A"),
        2: _identity(2, "A"),
        3: _identity(3, "B"),
        4: _identity(4, "B"),
    }
    expected = build_ability_families(observations, identities=identities)
    assert all(
        build_ability_families(order, identities=identities) == expected
        for order in permutations(observations)
    )


def test_conflicting_ownership_removes_both_families_from_spec_index() -> None:
    first = CanonicalAbility("Mage", "Fire", "A", (1,), (9,), FamilyProvenance.CURATED, False)
    second = CanonicalAbility("Mage", "Fire", "B", (2,), (9,), FamilyProvenance.CURATED, False)
    assert index_families_for_spec((first, second), "Mage", "Fire") == {}
