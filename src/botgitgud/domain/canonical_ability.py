"""Canonical cast/damage ability families derived from observed spec data."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from botgitgud.domain.ability_identity import AbilityIdentity
from botgitgud.domain.models import PlayerLog


class FamilyProvenance(StrEnum):
    DERIVED_NAME_MATCH = "derived_name_match"
    CURATED = "curated"


@dataclass(frozen=True, slots=True)
class CanonicalAbility:
    class_name: str
    spec_name: str
    resolved_name: str
    cast_ids: tuple[int, ...]
    damage_ids: tuple[int, ...]
    provenance: FamilyProvenance
    multi_cast: bool

    def __post_init__(self) -> None:
        if not self.resolved_name:
            raise ValueError("resolved_name must not be empty")
        if self.cast_ids != tuple(sorted(set(self.cast_ids))):
            raise ValueError("cast_ids must be sorted and unique")
        if self.damage_ids != tuple(sorted(set(self.damage_ids))):
            raise ValueError("damage_ids must be sorted and unique")
        if not set(self.cast_ids).isdisjoint(self.damage_ids):
            raise ValueError("cast_ids and damage_ids must be disjoint")
        if not self.cast_ids and not self.damage_ids:
            raise ValueError("at least one member ID is required")
        if self.multi_cast != (len(self.cast_ids) > 1):
            raise ValueError("multi_cast must equal (len(cast_ids) > 1)")


@dataclass(frozen=True, slots=True)
class SpecObservation:
    class_name: str
    spec_name: str
    cast_ids: frozenset[int]
    damage_ids: frozenset[int]


def observe_specs(logs: Sequence[PlayerLog]) -> tuple[SpecObservation, ...]:
    """Combine observations into one deterministic entry per class/spec."""
    grouped: dict[tuple[str, str], tuple[set[int], set[int]]] = {}
    for log in logs:
        key = (log.build.class_name, log.build.spec_name)
        casts, damage = grouped.setdefault(key, (set(), set()))
        casts.update(log.cast_timeline)
        damage.update(
            spell_id
            for spell_id, ability_damage in log.damage_by_ability.items()
            if ability_damage.total > 0
        )
    return tuple(
        SpecObservation(class_name, spec_name, frozenset(casts), frozenset(damage))
        for (class_name, spec_name), (casts, damage) in sorted(grouped.items())
    )


def build_ability_families(
    observations: Sequence[SpecObservation],
    *,
    identities: Mapping[int, AbilityIdentity],
) -> tuple[CanonicalAbility, ...]:
    """Associate resolved cast and damage IDs solely by exact name within a spec."""
    grouped: dict[tuple[str, str], tuple[set[int], set[int]]] = {}
    for observation in observations:
        key = (observation.class_name, observation.spec_name)
        casts, damage = grouped.setdefault(key, (set(), set()))
        casts.update(observation.cast_ids)
        damage.update(observation.damage_ids)

    result: list[CanonicalAbility] = []
    for (class_name, spec_name), (observed_casts, observed_damage) in sorted(grouped.items()):
        casts_by_name = _resolved_ids_by_name(observed_casts, identities)
        damage_by_name = _resolved_ids_by_name(observed_damage, identities)
        for resolved_name in sorted(casts_by_name.keys() & damage_by_name.keys()):
            cast_ids = casts_by_name[resolved_name]
            damage_ids = damage_by_name[resolved_name]
            if cast_ids & damage_ids:
                continue
            result.append(
                CanonicalAbility(
                    class_name=class_name,
                    spec_name=spec_name,
                    resolved_name=resolved_name,
                    cast_ids=tuple(sorted(cast_ids)),
                    damage_ids=tuple(sorted(damage_ids)),
                    provenance=FamilyProvenance.DERIVED_NAME_MATCH,
                    multi_cast=len(cast_ids) > 1,
                )
            )
    return tuple(result)


def _resolved_ids_by_name(
    spell_ids: set[int], identities: Mapping[int, AbilityIdentity]
) -> dict[str, set[int]]:
    result: dict[str, set[int]] = {}
    for spell_id in spell_ids:
        identity = identities.get(spell_id)
        if identity is not None and identity.resolution_status == "resolved":
            result.setdefault(identity.resolved_name, set()).add(spell_id)
    return result


def index_families_for_spec(
    families: Sequence[CanonicalAbility], class_name: str, spec_name: str
) -> Mapping[int, CanonicalAbility]:
    """Index only one class/spec, rejecting conflicting member ownership."""
    candidates: dict[int, list[CanonicalAbility]] = {}
    for family in families:
        if family.class_name == class_name and family.spec_name == spec_name:
            for spell_id in family.cast_ids + family.damage_ids:
                candidates.setdefault(spell_id, []).append(family)
    conflicted_families = {
        family for owners in candidates.values() if len(owners) > 1 for family in owners
    }
    return MappingProxyType(
        {
            spell_id: owners[0]
            for spell_id, owners in sorted(candidates.items())
            if len(owners) == 1 and owners[0] not in conflicted_families
        }
    )
