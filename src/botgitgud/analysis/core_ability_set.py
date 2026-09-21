"""Select at most ten actionable canonical ability entities per spec."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from botgitgud.analysis.ability_classification import classify_abilities
from botgitgud.analysis.canonical_role import index_canonical_roles_for_spec
from botgitgud.domain.ability_identity import AbilityIdentity
from botgitgud.domain.ability_role import AbilityRole
from botgitgud.domain.canonical_ability import (
    CanonicalAbility,
    FamilyProvenance,
    index_families_for_spec,
)
from botgitgud.domain.canonical_role import CanonicalAbilityRole
from botgitgud.domain.models import PlayerLog

MAX_CORE_ABILITIES = 10
MIN_LOG_COVERAGE = 0.05
ELIGIBLE_ROLES = frozenset(
    {
        AbilityRole.CORE_DAMAGE,
        AbilityRole.SECONDARY_DAMAGE,
        AbilityRole.INDIRECT_DAMAGE,
        AbilityRole.RESOURCE_GENERATOR,
        AbilityRole.RESOURCE_SPENDER,
        AbilityRole.PET_SUMMON,
        AbilityRole.OFFENSIVE_COOLDOWN,
        AbilityRole.SELF_OFFENSIVE_PROC,
        AbilityRole.SELF_OFFENSIVE_BUFF,
    }
)
TIER_ONE_ROLES = frozenset(
    {
        AbilityRole.PET_SUMMON,
        AbilityRole.OFFENSIVE_COOLDOWN,
        AbilityRole.SELF_OFFENSIVE_BUFF,
        AbilityRole.SELF_OFFENSIVE_PROC,
        AbilityRole.RESOURCE_GENERATOR,
        AbilityRole.RESOURCE_SPENDER,
    }
)
_MAINTAINED = frozenset({AbilityRole.SELF_OFFENSIVE_BUFF, AbilityRole.SELF_OFFENSIVE_PROC})


@dataclass(frozen=True, slots=True)
class CoreAbility:
    ability: CanonicalAbility
    role: AbilityRole
    evidence: tuple[str, ...]
    damage: float
    damage_share: float
    casts_per_log: float
    log_coverage: float


@dataclass(frozen=True, slots=True)
class DamageAccounting:
    core_damage: float
    actionable_unselected_damage: float
    non_actionable_damage: float

    @property
    def total_damage(self) -> float:
        return self.core_damage + self.actionable_unselected_damage + self.non_actionable_damage

    def shares(self) -> tuple[float, float, float]:
        total = self.total_damage
        if total <= 0:
            return (0.0, 0.0, 0.0)
        return (
            self.core_damage / total,
            self.actionable_unselected_damage / total,
            self.non_actionable_damage / total,
        )


@dataclass(frozen=True, slots=True)
class CoreAbilitySet:
    class_name: str
    spec_name: str
    candidate_count: int
    actionable_count: int
    abilities: tuple[CoreAbility, ...]
    actionable_damage: float
    accounting: DamageAccounting

    def __post_init__(self) -> None:
        if len(self.abilities) > MAX_CORE_ABILITIES:
            raise ValueError("CoreAbilitySet cannot contain more than 10 entities")

    @property
    def actionable_coverage(self) -> float:
        return (
            self.accounting.core_damage / self.actionable_damage
            if self.actionable_damage > 0
            else 1.0
        )


@dataclass(frozen=True, slots=True)
class _Observed:
    ability: CanonicalAbility
    members: tuple[int, ...]
    role: AbilityRole
    evidence: tuple[str, ...]
    damage: float
    casts_per_log: float
    log_coverage: float


def build_core_ability_sets(
    abilities: Sequence[CanonicalAbility],
    roles: Sequence[CanonicalAbilityRole],
    logs: Sequence[PlayerLog],
    *,
    identities: Mapping[int, AbilityIdentity],
) -> tuple[CoreAbilitySet, ...]:
    families = _unique_families(abilities)
    role_map = _unique_roles(roles)
    grouped: dict[tuple[str, str], list[PlayerLog]] = {}
    for log in logs:
        grouped.setdefault((log.build.class_name, log.build.spec_name), []).append(log)
    return tuple(
        _build(key, tuple(items), families, role_map, identities)
        for key, items in sorted(grouped.items())
    )


def build_core_ability_set(
    abilities: Sequence[CanonicalAbility],
    roles: Sequence[CanonicalAbilityRole],
    logs: Sequence[PlayerLog],
    *,
    identities: Mapping[int, AbilityIdentity],
) -> CoreAbilitySet:
    results = build_core_ability_sets(abilities, roles, logs, identities=identities)
    if len(results) != 1:
        raise ValueError("expected exactly one class/spec")
    return results[0]


def _build(
    spec: tuple[str, str],
    logs: tuple[PlayerLog, ...],
    families: Mapping[tuple[str, str, str], CanonicalAbility],
    roles: Mapping[tuple[str, str, str], CanonicalAbilityRole],
    identities: Mapping[int, AbilityIdentity],
) -> CoreAbilitySet:
    entities = _universe(spec, logs, families, roles, identities)
    family_index = dict(index_families_for_spec(tuple(families.values()), *spec))
    role_index = dict(index_canonical_roles_for_spec(tuple(roles.values()), *spec))
    counts: dict[str, Counter[AbilityRole]] = {name: Counter() for name in entities}
    for log in logs:
        for spell_id, classification in classify_abilities(
            log, families=family_index, canonical_roles=role_index
        ).items():
            identity = identities.get(spell_id)
            if identity is not None and identity.resolution_status == "resolved":
                family = family_index.get(spell_id)
                name = family.resolved_name if family is not None else identity.resolved_name
                counts.setdefault(name, Counter())[classification.role] += 1
    eligible: list[_Observed] = []
    for name, (ability, members) in entities.items():
        assigned = roles.get((*spec, name))
        role = assigned.role if assigned is not None else _dominant(counts[name])
        if role is None:
            continue
        if role not in ELIGIBLE_ROLES:
            continue
        evidence = _evidence(members, role, logs)
        damage = _damage(members, logs)
        casts = sum(len(log.cast_timeline.get(member, ())) for log in logs for member in members)
        coverage = _log_coverage(members, logs)
        if coverage < MIN_LOG_COVERAGE:
            continue
        eligible.append(
            _Observed(
                ability,
                members,
                role,
                evidence,
                damage,
                casts / len(logs) if logs else 0.0,
                coverage,
            )
        )
    actionable = [item for item in eligible if item.evidence]
    tier1 = sorted(
        (x for x in actionable if x.role in TIER_ONE_ROLES),
        key=lambda x: (-x.damage, -x.casts_per_log, x.ability.resolved_name),
    )
    tier2 = sorted(
        (x for x in actionable if x.role not in TIER_ONE_ROLES),
        key=lambda x: (-x.damage, x.ability.resolved_name),
    )
    selected = (tier1 + tier2)[:MAX_CORE_ABILITIES]
    names = {x.ability.resolved_name for x in selected}
    core_damage = sum(x.damage for x in selected)
    unselected = sum(x.damage for x in actionable if x.ability.resolved_name not in names)
    accounted = sum(max(d.total, 0.0) for log in logs for d in log.damage_by_ability.values())
    actionable_damage = sum(x.damage for x in actionable)
    return CoreAbilitySet(
        spec[0],
        spec[1],
        len(eligible),
        len(actionable),
        tuple(
            CoreAbility(
                x.ability,
                x.role,
                x.evidence,
                x.damage,
                x.damage / accounted if accounted > 0 else 0.0,
                x.casts_per_log,
                x.log_coverage,
            )
            for x in selected
        ),
        actionable_damage,
        DamageAccounting(core_damage, unselected, max(0.0, accounted - actionable_damage)),
    )


def _universe(
    spec: tuple[str, str],
    logs: Sequence[PlayerLog],
    families: Mapping[tuple[str, str, str], CanonicalAbility],
    roles: Mapping[tuple[str, str, str], CanonicalAbilityRole],
    identities: Mapping[int, AbilityIdentity],
) -> dict[str, tuple[CanonicalAbility, tuple[int, ...]]]:
    observed: set[int] = set()
    cast_ids: set[int] = set()
    for log in logs:
        cast_ids.update(log.cast_timeline)
        observed.update(log.cast_timeline)
        observed.update(log.damage_by_ability)
        observed.update(log.uptimes)
        observed.update(log.aura_details)
    spec_families = {
        name: family
        for (class_name, spec_name, name), family in families.items()
        if (class_name, spec_name) == spec
    }
    family_name_by_member = {
        spell_id: name
        for name, family in spec_families.items()
        for spell_id in family.cast_ids + family.damage_ids
    }
    by_name: dict[str, set[int]] = {}
    for spell_id in observed:
        identity = identities.get(spell_id)
        if identity is not None and identity.resolution_status == "resolved":
            name = family_name_by_member.get(spell_id, identity.resolved_name)
            by_name.setdefault(name, set()).add(spell_id)
    result: dict[str, tuple[CanonicalAbility, tuple[int, ...]]] = {}
    for name, member_set in sorted(by_name.items()):
        assigned = roles.get((*spec, name))
        if assigned is not None and assigned.role is None:
            continue
        family = spec_families.get(name)
        members = (
            tuple(sorted(family.cast_ids + family.damage_ids))
            if family is not None
            else tuple(sorted(member_set))
        )
        if family is None:
            casts = tuple(x for x in members if x in cast_ids)
            others = tuple(x for x in members if x not in cast_ids)
            family = CanonicalAbility(
                spec[0],
                spec[1],
                name,
                casts,
                others,
                FamilyProvenance.DERIVED_NAME_MATCH,
                len(casts) > 1,
            )
        result[name] = (family, members)
    return result


def _dominant(counts: Counter[AbilityRole]) -> AbilityRole | None:
    return min(counts, key=lambda role: (-counts[role], role.value)) if counts else None


def _evidence(
    members: Sequence[int], role: AbilityRole, logs: Sequence[PlayerLog]
) -> tuple[str, ...]:
    cast = any(member in log.cast_timeline for log in logs for member in members)
    aura = role in _MAINTAINED and any(
        member in log.uptimes or member in log.aura_details for log in logs for member in members
    )
    return tuple(label for label, yes in (("cast", cast), ("maintained_state", aura)) if yes)


def _damage(members: Sequence[int], logs: Sequence[PlayerLog]) -> float:
    return sum(
        max(log.damage_by_ability[x].total, 0.0)
        for log in logs
        for x in members
        if x in log.damage_by_ability
    )


def _log_coverage(members: Sequence[int], logs: Sequence[PlayerLog]) -> float:
    if not logs:
        return 0.0
    observed_logs = sum(
        any(
            member in log.cast_timeline or member in log.damage_by_ability or member in log.uptimes
            for member in members
        )
        for log in logs
    )
    return observed_logs / len(logs)


def _unique_families(
    items: Sequence[CanonicalAbility],
) -> dict[tuple[str, str, str], CanonicalAbility]:
    result: dict[tuple[str, str, str], CanonicalAbility] = {}
    for item in items:
        key = (item.class_name, item.spec_name, item.resolved_name)
        if key in result and result[key] != item:
            raise ValueError(f"conflicting canonical ability for {key!r}")
        result[key] = item
    return result


def _unique_roles(
    items: Sequence[CanonicalAbilityRole],
) -> dict[tuple[str, str, str], CanonicalAbilityRole]:
    result: dict[tuple[str, str, str], CanonicalAbilityRole] = {}
    for item in items:
        key = (item.class_name, item.spec_name, item.canonical_name)
        if key in result and result[key] != item:
            raise ValueError(f"conflicting canonical role for {key!r}")
        result[key] = item
    return result
