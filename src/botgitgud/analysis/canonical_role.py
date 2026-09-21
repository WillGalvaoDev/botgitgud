"""Pure derivation and spec-scoped indexing of canonical ability roles."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType

from botgitgud.analysis.ability_classification import CORE_DAMAGE_SHARE
from botgitgud.domain.ability_identity import AbilityIdentity
from botgitgud.domain.ability_overrides import ABILITY_OVERRIDES
from botgitgud.domain.ability_role import AbilityRole
from botgitgud.domain.canonical_ability import CanonicalAbility
from botgitgud.domain.canonical_role import CanonicalAbilityRole, RoleSource
from botgitgud.domain.curated_family_roles import CURATED_FAMILY_ROLES, CuratedFamilyRole
from botgitgud.domain.models import PlayerLog
from botgitgud.domain.non_spec_effects import NON_SPEC_EFFECT_ROLES


def derive_canonical_roles(
    logs: Sequence[PlayerLog],
    families: Sequence[CanonicalAbility],
    *,
    identities: Mapping[int, AbilityIdentity] = MappingProxyType({}),
    curated: Mapping[tuple[str, str, str], CuratedFamilyRole] = CURATED_FAMILY_ROLES,
) -> tuple[CanonicalAbilityRole, ...]:
    """Apply precedence and damage-share taxonomy to observed canonical entities."""
    totals: dict[tuple[str, str], float] = {}
    damage: dict[tuple[str, str, int], float] = {}
    partitions: dict[tuple[str, str], set[int | None]] = {}
    observed_by_name: dict[tuple[str, str, str], set[int]] = {}
    for log in logs:
        spec_key = (log.build.class_name, log.build.spec_name)
        partitions.setdefault(spec_key, set()).add(log.fight.partition)
        observed_ids = set(log.damage_by_ability) | set(log.cast_timeline) | set(log.uptimes)
        for spell_id in observed_ids:
            identity = identities.get(spell_id)
            if identity is not None and identity.resolution_status == "resolved":
                observed_by_name.setdefault((*spec_key, identity.resolved_name), set()).add(
                    spell_id
                )
        for spell_id, observed in log.damage_by_ability.items():
            if observed.total > 0.0:
                totals[spec_key] = totals.get(spec_key, 0.0) + observed.total
                key = (*spec_key, spell_id)
                damage[key] = damage.get(key, 0.0) + observed.total

    families_by_key = {
        (family.class_name, family.spec_name, family.resolved_name): family for family in families
    }
    entity_keys = set(families_by_key)
    entity_keys.update(key for key in curated if key in observed_by_name)

    result: list[CanonicalAbilityRole] = []
    for entity_key in sorted(entity_keys):
        family = families_by_key.get(entity_key)
        member_ids = tuple(
            sorted(
                set(family.cast_ids + family.damage_ids)
                if family is not None
                else observed_by_name[entity_key]
            )
        )

        matching_overrides = {
            override.role
            for spell_id in member_ids
            if (override := ABILITY_OVERRIDES.get(spell_id)) is not None
            and override.partition in partitions.get(entity_key[:2], set())
        }
        if len(matching_overrides) == 1:
            role = next(iter(matching_overrides))
            result.append(
                CanonicalAbilityRole(
                    *entity_key,
                    role,
                    RoleSource.CURATED,
                    ("ability_override",),
                    member_ids,
                )
            )
            continue

        non_spec_roles = {
            NON_SPEC_EFFECT_ROLES[spell_id]
            for spell_id in member_ids
            if spell_id in NON_SPEC_EFFECT_ROLES
        }
        if len(non_spec_roles) == 1:
            role = next(iter(non_spec_roles))
            result.append(
                CanonicalAbilityRole(
                    *entity_key,
                    role,
                    RoleSource.CURATED,
                    ("non_spec_effect_role",),
                    member_ids,
                )
            )
            continue

        curated_role = curated.get(entity_key)
        if curated_role is not None:
            result.append(
                CanonicalAbilityRole(
                    *entity_key,
                    curated_role.role,
                    RoleSource.CURATED,
                    (f"curated:{curated_role.provenance}", curated_role.justification),
                    member_ids,
                )
            )
            continue

        spec_total = totals.get(entity_key[:2], 0.0)
        family_total = (
            sum(damage.get((*entity_key[:2], spell_id), 0.0) for spell_id in family.damage_ids)
            if family is not None
            else 0.0
        )
        if spec_total > 0.0 and family_total > 0.0:
            share = family_total / spec_total
            role = (
                AbilityRole.CORE_DAMAGE
                if share >= CORE_DAMAGE_SHARE
                else AbilityRole.SECONDARY_DAMAGE
            )
            result.append(
                CanonicalAbilityRole(
                    *entity_key,
                    role,
                    RoleSource.DERIVED,
                    (f"family_damage_share={share:.2%}",),
                    member_ids,
                )
            )
        else:
            result.append(
                CanonicalAbilityRole(*entity_key, None, RoleSource.UNRESOLVED, (), member_ids)
            )
    return tuple(result)


def index_canonical_roles_for_spec(
    roles: Sequence[CanonicalAbilityRole],
    class_name: str,
    spec_name: str,
) -> Mapping[int, CanonicalAbilityRole]:
    """Project roles to members within exactly one class/spec partition."""
    candidates_by_id: dict[int, list[CanonicalAbilityRole]] = {}
    for role in roles:
        if role.class_name == class_name and role.spec_name == spec_name:
            for spell_id in role.member_ids:
                candidates_by_id.setdefault(spell_id, []).append(role)
    conflicted_entities = {
        role for owners in candidates_by_id.values() if len(owners) > 1 for role in owners
    }
    return MappingProxyType(
        {
            spell_id: owners[0]
            for spell_id, owners in sorted(candidates_by_id.items())
            if len(owners) == 1 and owners[0] not in conflicted_entities
        }
    )
