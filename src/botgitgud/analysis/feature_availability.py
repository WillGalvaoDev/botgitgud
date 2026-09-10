"""Decide which ability features are observable in one player log."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from botgitgud.domain.ability_identity import AbilityIdentity
from botgitgud.domain.ability_role import AbilityRole
from botgitgud.domain.canonical_ability import CanonicalAbility
from botgitgud.domain.models import PlayerLog


class FeatureKind(StrEnum):
    DAMAGE_SHARE = "damage_share"
    CAST_COUNT = "cast_count"
    CASTS_PER_MINUTE = "casts_per_minute"
    CAST_TIMELINE = "cast_timeline"
    UPTIME = "uptime"


class FeatureBlockReason(StrEnum):
    NO_SIGNAL = "no_signal"
    UNRESOLVED_IDENTITY = "unresolved_identity"
    NON_SPEC_ROLE = "non_spec_role"
    NO_DURATION = "no_duration"


@dataclass(frozen=True, slots=True)
class FeatureAvailability:
    kind: FeatureKind
    available: bool
    reasons: tuple[FeatureBlockReason, ...]

    def __post_init__(self) -> None:
        if self.available != (not self.reasons):
            raise ValueError("reasons must be empty exactly when the feature is available")


_NON_SPEC_ROLES = frozenset(
    {
        AbilityRole.UNKNOWN,
        AbilityRole.DEFENSIVE,
        AbilityRole.UTILITY,
        AbilityRole.HEALING,
        AbilityRole.CONSUMABLE,
        AbilityRole.EQUIPMENT_EFFECT,
        AbilityRole.RACIAL,
        AbilityRole.EXTERNAL_OFFENSIVE,
        AbilityRole.EXTERNAL_NON_OFFENSIVE,
        AbilityRole.SELF_AURA_UNRESOLVED,
    }
)


def evaluate_feature_availability(
    ability: CanonicalAbility,
    role: AbilityRole | None,
    log: PlayerLog,
    *,
    identities: Mapping[int, AbilityIdentity],
) -> tuple[FeatureAvailability, ...]:
    """Return all feature decisions for ``ability`` in the context of ``log``."""
    members = ability.cast_ids + ability.damage_ids
    has_resolved_identity = any(
        (identity := identities.get(spell_id)) is not None
        and identity.resolution_status == "resolved"
        for spell_id in members
    )
    has_cast = any(log.cast_timeline.get(spell_id, ()) for spell_id in members)
    has_damage = any(
        (damage := log.damage_by_ability.get(spell_id)) is not None and damage.total > 0
        for spell_id in members
    )
    has_uptime = any(spell_id in log.uptimes for spell_id in members)

    signal_by_kind = {
        FeatureKind.DAMAGE_SHARE: has_damage,
        FeatureKind.CAST_COUNT: has_cast,
        FeatureKind.CASTS_PER_MINUTE: has_cast,
        FeatureKind.CAST_TIMELINE: has_cast,
        FeatureKind.UPTIME: has_uptime,
    }

    result: list[FeatureAvailability] = []
    for kind in sorted(FeatureKind, key=lambda item: item.value):
        reasons: list[FeatureBlockReason] = []
        if not has_resolved_identity:
            reasons.append(FeatureBlockReason.UNRESOLVED_IDENTITY)
        if role is None or role in _NON_SPEC_ROLES:
            reasons.append(FeatureBlockReason.NON_SPEC_ROLE)
        if not signal_by_kind[kind]:
            reasons.append(FeatureBlockReason.NO_SIGNAL)
        if kind is FeatureKind.CASTS_PER_MINUTE and log.fight.duration_s <= 0:
            reasons.append(FeatureBlockReason.NO_DURATION)
        ordered_reasons = tuple(sorted(reasons, key=lambda reason: reason.value))
        result.append(
            FeatureAvailability(kind=kind, available=not ordered_reasons, reasons=ordered_reasons)
        )
    return tuple(result)
