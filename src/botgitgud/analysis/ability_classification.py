"""Pure, fail-closed classification from the evidence in one player log."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

import structlog

from botgitgud.analysis.cadence import _interval_stats
from botgitgud.domain.ability_identity import AbilityIdentity
from botgitgud.domain.ability_overrides import ABILITY_OVERRIDES
from botgitgud.domain.ability_role import (
    EXTERNAL_NON_OFFENSIVE_IDS,
    EXTERNAL_OFFENSIVE_IDS,
    AbilityRole,
)
from botgitgud.domain.canonical_ability import CanonicalAbility
from botgitgud.domain.canonical_role import CanonicalAbilityRole
from botgitgud.domain.contextual_spec_roles import CONTEXTUAL_SPEC_ROLES
from botgitgud.domain.models import PlayerLog
from botgitgud.domain.non_spec_effects import NON_SPEC_EFFECT_ROLES

CORE_DAMAGE_SHARE = 0.03
OFFENSIVE_CD_INTERVAL_S = 45.0
MIN_CASTS_FOR_CADENCE = 2

logger = structlog.get_logger(__name__)


class OffensiveEvidence(StrEnum):
    CANONICAL_FAMILY_DAMAGE = "canonical_family_damage"


@dataclass(frozen=True, slots=True)
class AbilitySignals:
    """Raw observations preserve enough evidence to audit the chosen rule."""

    has_damage: bool
    damage_share: float
    was_cast_by_player: bool
    cast_count: int
    is_aura_on_player: bool
    median_cast_interval_s: float | None


@dataclass(frozen=True, slots=True)
class AbilityClassification:
    """An assigned role together with the observations and deciding rule."""

    spell_id: int
    role: AbilityRole
    signals: AbilitySignals
    rule: str
    from_override: bool


def _decision(
    spell_id: int,
    role: AbilityRole,
    signals: AbilitySignals,
    rule: str,
    *,
    from_override: bool = False,
) -> AbilityClassification:
    return AbilityClassification(spell_id, role, signals, rule, from_override)


def _offensive_evidence(
    spell_id: int, families: Mapping[int, CanonicalAbility]
) -> frozenset[OffensiveEvidence]:
    family = families.get(spell_id)
    if family is not None and family.damage_ids:
        return frozenset({OffensiveEvidence.CANONICAL_FAMILY_DAMAGE})
    return frozenset()


def classify_abilities(
    log: PlayerLog,
    *,
    families: Mapping[int, CanonicalAbility] = MappingProxyType({}),
    canonical_roles: Mapping[int, CanonicalAbilityRole] = MappingProxyType({}),
) -> Mapping[int, AbilityClassification]:
    """Classify every observable spell using only evidence carried by ``log``."""
    spell_ids = set(log.damage_by_ability) | set(log.cast_timeline) | set(log.uptimes)
    positive_damage_total = sum(
        damage.total for damage in log.damage_by_ability.values() if damage.total > 0.0
    )
    classifications: dict[int, AbilityClassification] = {}

    for spell_id in sorted(spell_ids):
        damage = log.damage_by_ability.get(spell_id)
        has_damage = damage is not None and damage.total > 0.0
        damage_share = (
            damage.total / positive_damage_total
            if has_damage and positive_damage_total > 0.0 and damage is not None
            else 0.0
        )
        cast_times = log.cast_timeline.get(spell_id, ())
        cast_count = len(cast_times)
        median_interval, _iqr = _interval_stats(sorted(cast_times))
        signals = AbilitySignals(
            has_damage=has_damage,
            damage_share=damage_share,
            was_cast_by_player=spell_id in log.cast_timeline,
            cast_count=cast_count,
            is_aura_on_player=spell_id in log.uptimes,
            median_cast_interval_s=median_interval,
        )

        override = ABILITY_OVERRIDES.get(spell_id)
        if override is not None and override.partition == log.fight.partition:
            logger.info(
                "ability_classification.override_applied",
                spell_id=spell_id,
                partition=log.fight.partition,
                role=override.role,
            )
            classifications[spell_id] = _decision(
                spell_id, override.role, signals, "rule_1_override", from_override=True
            )
            continue
        if override is not None:
            logger.warning(
                "ability_classification.stale_override",
                spell_id=spell_id,
                override_partition=override.partition,
                observed_partition=log.fight.partition,
            )

        contextual_role = CONTEXTUAL_SPEC_ROLES.get(
            (log.build.class_name, log.build.spec_name, spell_id)
        )
        if contextual_role is not None:
            classifications[spell_id] = _decision(
                spell_id, contextual_role, signals, "rule_contextual_spec_role"
            )
            continue

        non_spec_role = NON_SPEC_EFFECT_ROLES.get(spell_id)
        if non_spec_role is not None:
            classifications[spell_id] = _decision(
                spell_id, non_spec_role, signals, "rule_non_spec_effect"
            )
            continue

        canonical_role = canonical_roles.get(spell_id)
        if canonical_role is not None and canonical_role.role is not None:
            classifications[spell_id] = _decision(
                spell_id,
                canonical_role.role,
                signals,
                f"rule_canonical_role:{canonical_role.role_source.value}",
            )
            continue

        if has_damage and signals.was_cast_by_player:
            if damage_share >= CORE_DAMAGE_SHARE:
                classifications[spell_id] = _decision(
                    spell_id, AbilityRole.CORE_DAMAGE, signals, "rule_2_core"
                )
            else:
                classifications[spell_id] = _decision(
                    spell_id, AbilityRole.SECONDARY_DAMAGE, signals, "rule_2_secondary"
                )
        elif has_damage:
            classifications[spell_id] = _decision(
                spell_id, AbilityRole.INDIRECT_DAMAGE, signals, "rule_3_indirect"
            )
        elif (
            signals.was_cast_by_player
            and cast_count >= MIN_CASTS_FOR_CADENCE
            and median_interval is not None
            and median_interval >= OFFENSIVE_CD_INTERVAL_S
        ):
            evidence = _offensive_evidence(spell_id, families)
            if evidence:
                evidence_names = ",".join(sorted(item.value for item in evidence))
                classifications[spell_id] = _decision(
                    spell_id,
                    AbilityRole.OFFENSIVE_COOLDOWN,
                    signals,
                    f"rule_4_offensive_cd:{evidence_names}",
                )
            else:
                classifications[spell_id] = _decision(
                    spell_id, AbilityRole.UNKNOWN, signals, "rule_4_unknown_no_evidence"
                )
        elif signals.is_aura_on_player and not signals.was_cast_by_player:
            if spell_id in EXTERNAL_OFFENSIVE_IDS:
                role = AbilityRole.EXTERNAL_OFFENSIVE
                rule = "rule_5_external_offensive"
            elif spell_id in EXTERNAL_NON_OFFENSIVE_IDS:
                role = AbilityRole.EXTERNAL_NON_OFFENSIVE
                rule = "rule_5_external_non_offensive"
            else:
                role = AbilityRole.SELF_AURA_UNRESOLVED
                rule = "rule_5_self_aura_unresolved"
            classifications[spell_id] = _decision(spell_id, role, signals, rule)
        else:
            classifications[spell_id] = _decision(
                spell_id, AbilityRole.UNKNOWN, signals, "rule_6_unknown"
            )

    return classifications


def classify_resolved_abilities(
    log: PlayerLog, *, identities: Mapping[int, AbilityIdentity]
) -> Mapping[int, AbilityClassification]:
    """Classify only abilities whose identity has been resolved."""
    return {
        spell_id: classification
        for spell_id, classification in classify_abilities(log).items()
        if (identity := identities.get(spell_id)) is not None
        and identity.resolution_status == "resolved"
    }
