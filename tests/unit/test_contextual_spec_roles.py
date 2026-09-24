from __future__ import annotations

from dataclasses import replace
from functools import cache
from pathlib import Path
from typing import NamedTuple

import pytest
from real_corpus import require_real_corpus

from botgitgud.analysis import ability_classification
from botgitgud.analysis.ability_classification import classify_abilities
from botgitgud.analysis.canonical_role import derive_canonical_roles
from botgitgud.analysis.core_ability_set import (
    ELIGIBLE_ROLES,
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
from botgitgud.domain.canonical_role import CanonicalAbilityRole
from botgitgud.domain.contextual_spec_roles import CONTEXTUAL_SPEC_ROLES
from botgitgud.domain.damage_scope import DamageScopeVersion
from botgitgud.domain.models import PlayerLog
from botgitgud.domain.specs import SpecId, SpecSupport, classify_spec
from botgitgud.domain.spells import SpellCatalog

SPELL_ID = 434481
DEVASTATION = ("Evoker", "Devastation")


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
    observed = set().union(
        *(set(log.cast_timeline) | set(log.damage_by_ability) | set(log.uptimes) for log in logs)
    )
    identities = {spell_id: catalog.identity(spell_id) for spell_id in observed}
    families = build_ability_families(observe_specs(logs), identities=identities)
    roles = derive_canonical_roles(logs, families, identities=identities)
    core_sets = build_core_ability_sets(families, roles, logs, identities=identities)
    return _Pipeline(logs, identities, families, roles, core_sets)


def _observes(log: PlayerLog) -> bool:
    return (
        SPELL_ID in log.damage_by_ability
        or SPELL_ID in log.cast_timeline
        or SPELL_ID in log.uptimes
    )


def test_contextual_roles_cover_exactly_observed_supported_specs() -> None:
    logs, *_ = _pipeline()
    observed_specs = {(log.build.class_name, log.build.spec_name) for log in logs if _observes(log)}
    table_specs = {(class_name, spec_name) for class_name, spec_name, _ in CONTEXTUAL_SPEC_ROLES}
    assert table_specs == observed_specs
    assert len(CONTEXTUAL_SPEC_ROLES) == len(table_specs)
    assert all(spell_id == SPELL_ID for _, _, spell_id in CONTEXTUAL_SPEC_ROLES)
    assert all(
        classify_spec(SpecId(class_name, spec_name)) is SpecSupport.SUPPORTED
        for class_name, spec_name in table_specs
    )
    assert ("Evoker", "Augmentation") not in table_specs


def test_every_observation_uses_the_reviewed_contextual_decision() -> None:
    logs, *_ = _pipeline()
    observing_logs = [log for log in logs if _observes(log)]
    assert observing_logs
    for log in observing_logs:
        spec = (log.build.class_name, log.build.spec_name)
        decision = classify_abilities(log)[SPELL_ID]
        expected = (
            AbilityRole.INDIRECT_DAMAGE if spec == DEVASTATION else AbilityRole.EXTERNAL_OFFENSIVE
        )
        assert decision.role is expected
        assert decision.rule == "rule_contextual_spec_role"
    assert any((log.build.class_name, log.build.spec_name) == DEVASTATION for log in observing_logs)
    assert {
        (log.build.class_name, log.build.spec_name)
        for log in observing_logs
        if (log.build.class_name, log.build.spec_name) != DEVASTATION
    } == {key[:2] for key in CONTEXTUAL_SPEC_ROLES if key[:2] != DEVASTATION}


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


def test_external_recipients_block_all_features_but_devastation_keeps_damage_feature() -> None:
    logs, identities, *_ = _pipeline()
    for log in (item for item in logs if _observes(item)):
        spec = (log.build.class_name, log.build.spec_name)
        ability = CanonicalAbility(
            *spec,
            identities[SPELL_ID].resolved_name,
            (),
            (SPELL_ID,),
            FamilyProvenance.DERIVED_NAME_MATCH,
            False,
        )
        role = classify_abilities(log)[SPELL_ID].role
        features = evaluate_feature_availability(ability, role, log, identities=identities)
        assert {feature.kind for feature in features} == set(FeatureKind)
        if spec == DEVASTATION:
            damage = next(item for item in features if item.kind is FeatureKind.DAMAGE_SHARE)
            assert damage.available is (log.damage_scope is DamageScopeVersion.WCL_TARGET_SCOPE_V1)
            # Same contextual role with an explicitly reconciled synthetic copy.
            synthetic = replace(log, damage_scope=DamageScopeVersion.WCL_TARGET_SCOPE_V1)
            positive = evaluate_feature_availability(
                ability, role, synthetic, identities=identities
            )
            assert next(f for f in positive if f.kind is FeatureKind.DAMAGE_SHARE).available
        else:
            assert all(not feature.available for feature in features)
            assert all(FeatureBlockReason.NON_SPEC_ROLE in feature.reasons for feature in features)


def test_contextual_id_never_enters_canonical_roles_or_core_sets() -> None:
    _logs, _identities, _families, roles, core_sets = _pipeline()
    assert all(SPELL_ID not in role.member_ids for role in roles)
    assert all(
        SPELL_ID not in ability.ability.cast_ids + ability.ability.damage_ids
        for core_set in core_sets
        for ability in core_set.abilities
    )
    assert ABILITY_OVERRIDES == {}


def test_devastation_and_demonology_core_sets_match_pre_contextual_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs, identities, families, roles, current = _pipeline()
    selected = {DEVASTATION, ("Warlock", "Demonology")}
    current_by_spec = {
        (item.class_name, item.spec_name): item
        for item in current
        if (item.class_name, item.spec_name) in selected
    }
    monkeypatch.setattr(ability_classification, "CONTEXTUAL_SPEC_ROLES", {})
    baseline = build_core_ability_sets(
        families,
        roles,
        [log for log in logs if (log.build.class_name, log.build.spec_name) in selected],
        identities=identities,
    )
    baseline_by_spec = {(item.class_name, item.spec_name): item for item in baseline}
    assert set(baseline_by_spec) == set(current_by_spec) == selected
    for spec in selected:
        before = baseline_by_spec[spec]
        after = current_by_spec[spec]
        assert tuple(
            (ability.ability.resolved_name, ability.role) for ability in before.abilities
        ) == tuple((ability.ability.resolved_name, ability.role) for ability in after.abilities)
        assert before.accounting == after.accounting
        made_ineligible = {
            identities[spell_id].resolved_name
            for (class_name, spec_name, spell_id), role in CONTEXTUAL_SPEC_ROLES.items()
            if (class_name, spec_name) == spec
            and role not in ELIGIBLE_ROLES
            and any(
                spell_id in log.damage_by_ability
                or spell_id in log.cast_timeline
                or spell_id in log.uptimes
                for log in logs
                if (log.build.class_name, log.build.spec_name) == spec
            )
        }
        assert before.candidate_count - after.candidate_count == len(made_ineligible)
