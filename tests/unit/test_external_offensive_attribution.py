from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import NamedTuple

from real_corpus import require_real_corpus

from botgitgud.analysis.ability_classification import classify_abilities
from botgitgud.analysis.canonical_role import derive_canonical_roles
from botgitgud.analysis.core_ability_set import (
    ELIGIBLE_ROLES,
    MIN_LOG_COVERAGE,
    CoreAbilitySet,
    build_core_ability_sets,
)
from botgitgud.analysis.feature_availability import (
    FeatureBlockReason,
    FeatureKind,
    evaluate_feature_availability,
)
from botgitgud.domain.ability_identity import AbilityIdentity
from botgitgud.domain.ability_overrides import ABILITY_OVERRIDES
from botgitgud.domain.ability_role import AbilityRole
from botgitgud.domain.canonical_ability import (
    CanonicalAbility,
    FamilyProvenance,
    build_ability_families,
    observe_specs,
)
from botgitgud.domain.canonical_role import CanonicalAbilityRole, RoleSource
from botgitgud.domain.models import PlayerLog
from botgitgud.domain.non_spec_effects import NON_SPEC_EFFECT_ROLES
from botgitgud.domain.spells import SpellCatalog

DECIDED_ROLES = {
    409632: AbilityRole.EXTERNAL_OFFENSIVE,
    404908: AbilityRole.EXTERNAL_OFFENSIVE,
    410265: AbilityRole.EXTERNAL_OFFENSIVE,
    410263: AbilityRole.EXTERNAL_NON_OFFENSIVE,
}
NON_PROPAGATED_IDS = {403631, 442204, 434473, 413786, 1303071, 395296, 409311}


class _Pipeline(NamedTuple):
    logs: tuple[PlayerLog, ...]
    identities: dict[int, AbilityIdentity]
    families: tuple[CanonicalAbility, ...]
    roles: tuple[CanonicalAbilityRole, ...]
    core_sets: tuple[CoreAbilitySet, ...]


@cache
def _pipeline() -> _Pipeline:
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
    core_sets = build_core_ability_sets(families, roles, logs, identities=identities)
    return _Pipeline(logs, identities, families, roles, core_sets)


def _observes(log: PlayerLog, spell_id: int) -> bool:
    return (
        spell_id in log.cast_timeline
        or spell_id in log.damage_by_ability
        or spell_id in log.uptimes
    )


def test_decided_roles_precede_indirect_damage_rule_in_every_observing_log() -> None:
    logs, *_ = _pipeline()
    for spell_id, role in DECIDED_ROLES.items():
        observing_logs = [log for log in logs if _observes(log, spell_id)]
        assert observing_logs
        for log in observing_logs:
            decision = classify_abilities(log)[spell_id]
            assert decision.role is role
            assert decision.rule == "rule_non_spec_effect"


def test_decided_effects_are_absent_from_every_core_ability_set() -> None:
    *_, core_sets = _pipeline()
    assert len(core_sets) == len({(item.class_name, item.spec_name) for item in core_sets}) == 25
    assert all(
        DECIDED_ROLES.keys().isdisjoint(item.ability.cast_ids + item.ability.damage_ids)
        for core_set in core_sets
        for item in core_set.abilities
    )


def test_decided_effects_expose_zero_actionable_features() -> None:
    logs, identities, *_ = _pipeline()
    for spell_id, role in DECIDED_ROLES.items():
        for log in (item for item in logs if _observes(item, spell_id)):
            identity = identities[spell_id]
            ability = CanonicalAbility(
                log.build.class_name,
                log.build.spec_name,
                identity.resolved_name,
                (spell_id,) if spell_id in log.cast_timeline else (),
                () if spell_id in log.cast_timeline else (spell_id,),
                FamilyProvenance.DERIVED_NAME_MATCH,
                False,
            )
            decisions = evaluate_feature_availability(ability, role, log, identities=identities)
            assert {decision.kind for decision in decisions} == set(FeatureKind)
            assert all(not decision.available for decision in decisions)
            assert all(
                FeatureBlockReason.NON_SPEC_ROLE in decision.reasons for decision in decisions
            )


def test_positive_damage_observations_are_preserved_exactly() -> None:
    logs, *_ = _pipeline()
    observed = {
        (log.build.class_name, log.build.spec_name, spell_id)
        for log in logs
        for spell_id, damage in log.damage_by_ability.items()
        if damage.total > 0
    }
    classified = {
        (log.build.class_name, log.build.spec_name, spell_id)
        for log in logs
        for spell_id, decision in classify_abilities(log).items()
        if decision.signals.has_damage
    }
    assert classified == observed


def test_breath_of_eons_demonology_is_excluded_semantically_not_by_coverage() -> None:
    logs, *_, core_sets = _pipeline()
    demonology_logs = [
        log
        for log in logs
        if (log.build.class_name, log.build.spec_name) == ("Warlock", "Demonology")
    ]
    assert demonology_logs
    coverage = sum(_observes(log, 409632) for log in demonology_logs) / len(demonology_logs)
    assert coverage >= MIN_LOG_COVERAGE
    assert DECIDED_ROLES[409632] is AbilityRole.EXTERNAL_OFFENSIVE
    assert AbilityRole.EXTERNAL_OFFENSIVE not in ELIGIBLE_ROLES
    (demonology_core,) = (
        item for item in core_sets if (item.class_name, item.spec_name) == ("Warlock", "Demonology")
    )
    assert all(
        409632 not in item.ability.cast_ids + item.ability.damage_ids
        for item in demonology_core.abilities
    )


def test_unresolved_ids_remain_fail_closed_and_overrides_remain_empty() -> None:
    *_, roles, _core_sets = _pipeline()
    unresolved = [role for role in roles if role.role_source is RoleSource.UNRESOLVED]
    assert all(
        role.role is None and set(role.member_ids).isdisjoint(DECIDED_ROLES) for role in unresolved
    )
    assert roles
    assert all((role.role is None) is (role.role_source is RoleSource.UNRESOLVED) for role in roles)
    # Authority for the decided IDs is classify_abilities (RB-5.1), not canonical roles.
    assert all(set(role.member_ids).isdisjoint(DECIDED_ROLES) for role in roles)
    assert ABILITY_OVERRIDES == {}


def test_catalog_neighbors_are_not_propagated_into_curated_roles() -> None:
    assert NON_PROPAGATED_IDS.isdisjoint(NON_SPEC_EFFECT_ROLES)
