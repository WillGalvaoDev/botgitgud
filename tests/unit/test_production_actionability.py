from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import NamedTuple

from real_corpus import require_real_corpus

from botgitgud.analysis.ability_classification import (
    MIN_CASTS_FOR_CADENCE,
    OFFENSIVE_CD_INTERVAL_S,
    AbilityClassification,
    classify_abilities,
)
from botgitgud.analysis.canonical_role import (
    derive_canonical_roles,
    index_canonical_roles_for_spec,
)
from botgitgud.domain.ability_role import ACTIONABLE_ROLES, AbilityRole
from botgitgud.domain.canonical_ability import (
    CanonicalAbility,
    build_ability_families,
    index_families_for_spec,
    observe_specs,
)
from botgitgud.domain.canonical_role import CanonicalAbilityRole
from botgitgud.domain.models import PlayerLog
from botgitgud.domain.spells import SpellCatalog


class _ProductionClassification(NamedTuple):
    log: PlayerLog
    raw: dict[int, AbilityClassification]
    family_index: dict[int, CanonicalAbility]
    role_index: dict[int, CanonicalAbilityRole]
    projected: dict[int, AbilityClassification]


class _ProductionCorpus(NamedTuple):
    rows: tuple[_ProductionClassification, ...]
    families: tuple[CanonicalAbility, ...]
    roles: tuple[CanonicalAbilityRole, ...]


@cache
def _production_corpus() -> _ProductionCorpus:
    """Exercise the complete production ordering without replacing any stage."""
    logs = tuple(require_real_corpus())
    catalog_path = Path(__file__).resolve().parents[2] / "data" / "spells.json"
    catalog = SpellCatalog(catalog_path, blizzard=None)
    observed_ids = set().union(
        *(set(log.damage_by_ability) | set(log.cast_timeline) | set(log.uptimes) for log in logs)
    )
    identities = {spell_id: catalog.identity(spell_id) for spell_id in observed_ids}

    # Raw classification is intentionally retained: besides proving the first
    # stage ran, it lets the assertions distinguish observed actionability from
    # the semantic role projected later.
    raw = tuple(dict(classify_abilities(log)) for log in logs)
    families = build_ability_families(observe_specs(logs), identities=identities)
    roles = derive_canonical_roles(logs, families, identities=identities)
    rows: list[_ProductionClassification] = []
    for log, raw_result in zip(logs, raw, strict=True):
        family_index = dict(
            index_families_for_spec(families, log.build.class_name, log.build.spec_name)
        )
        role_index = dict(
            index_canonical_roles_for_spec(roles, log.build.class_name, log.build.spec_name)
        )
        projected = dict(classify_abilities(log, families=family_index, canonical_roles=role_index))
        rows.append(_ProductionClassification(log, raw_result, family_index, role_index, projected))
    return _ProductionCorpus(tuple(rows), families, roles)


def _aura_only_rows(
    spell_id: int, class_name: str, spec_name: str
) -> list[_ProductionClassification]:
    return [
        row
        for row in _production_corpus().rows
        if row.log.build.class_name == class_name
        and row.log.build.spec_name == spec_name
        and spell_id in row.raw
        and row.raw[spell_id].signals.is_aura_on_player
        and not row.raw[spell_id].signals.was_cast_by_player
        and not row.raw[spell_id].signals.has_damage
    ]


def test_starfall_aura_member_does_not_veto_actionable_entity_role() -> None:
    rows = _aura_only_rows(191034, "Druid", "Balance")
    assert rows
    assert all(row.raw[191034].role is AbilityRole.SELF_AURA_UNRESOLVED for row in rows)
    assert all(row.projected[191034].role is AbilityRole.CORE_DAMAGE for row in rows)
    assert all(row.projected[191034].rule == "rule_canonical_role:derived" for row in rows)


def test_lightning_shield_aura_member_does_not_veto_actionable_entity_role() -> None:
    rows = _aura_only_rows(192106, "Shaman", "Enhancement")
    assert rows
    assert all(row.raw[192106].role is AbilityRole.SELF_AURA_UNRESOLVED for row in rows)
    assert all(row.projected[192106].role is AbilityRole.SECONDARY_DAMAGE for row in rows)
    assert all(row.projected[192106].rule == "rule_canonical_role:derived" for row in rows)


def test_lightning_shield_without_a_family_in_elemental_remains_non_actionable() -> None:
    rows = _aura_only_rows(192106, "Shaman", "Elemental")
    assert rows
    assert all(192106 not in row.family_index for row in rows)
    assert all(192106 not in row.role_index for row in rows)
    assert all(row.projected[192106].role not in ACTIONABLE_ROLES for row in rows)


def test_real_aura_only_entity_without_positive_evidence_remains_non_actionable() -> None:
    matches = [
        (spell_id, row)
        for row in _production_corpus().rows
        for spell_id, classification in row.raw.items()
        if classification.signals.is_aura_on_player
        and not classification.signals.was_cast_by_player
        and not classification.signals.has_damage
        and spell_id not in row.family_index
        and spell_id not in row.role_index
    ]
    assert matches, "the real corpus must contain an unassociated aura-only entity"
    spell_id, row = matches[0]
    assert row.projected[spell_id].role not in ACTIONABLE_ROLES


def test_unknown_remains_fail_closed_after_production_projection() -> None:
    matches = [
        (spell_id, row)
        for row in _production_corpus().rows
        for spell_id, classification in row.raw.items()
        if classification.role is AbilityRole.UNKNOWN and spell_id not in row.role_index
    ]
    assert matches
    assert all(row.projected[spell_id].role is AbilityRole.UNKNOWN for spell_id, row in matches)


def test_aura_member_does_not_create_a_second_actionable_entity() -> None:
    for name, aura_id in (("Starfall", 191034), ("Lightning Shield", 192106)):
        entity_roles = [
            role
            for role in _production_corpus().roles
            if role.canonical_name == name and aura_id in role.member_ids
        ]
        assert len(entity_roles) == 1
        assert entity_roles[0].role in ACTIONABLE_ROLES


def test_canonical_members_observe_one_semantic_role_not_independent_roles() -> None:
    for name in ("Starfall", "Lightning Shield"):
        entities = {
            (role.class_name, role.spec_name, role.canonical_name)
            for role in _production_corpus().roles
            if role.canonical_name == name
        }
        assert len(entities) == 1
        role = next(role for role in _production_corpus().roles if role.canonical_name == name)
        indexes = [
            row.role_index
            for row in _production_corpus().rows
            if (row.log.build.class_name, row.log.build.spec_name)
            == (role.class_name, role.spec_name)
        ]
        assert indexes
        assert all({index[member] for member in role.member_ids} == {role} for index in indexes)


def test_original_cadence_only_false_positive_set_is_not_reintroduced_by_cadence() -> None:
    cadence_only_ids = {
        spell_id
        for row in _production_corpus().rows
        for spell_id, classification in row.raw.items()
        if not classification.signals.has_damage
        and classification.signals.cast_count >= MIN_CASTS_FOR_CADENCE
        and classification.signals.median_cast_interval_s is not None
        and classification.signals.median_cast_interval_s >= OFFENSIVE_CD_INTERVAL_S
    }
    assert cadence_only_ids

    projected_candidates = [
        row.projected[spell_id]
        for row in _production_corpus().rows
        for spell_id in cadence_only_ids
        if spell_id in row.projected
    ]
    assert all(
        not classification.rule.startswith("rule_4_offensive_cd")
        for classification in projected_candidates
    )
    assert any(
        classification.role is AbilityRole.OFFENSIVE_COOLDOWN
        and classification.rule == "rule_canonical_role:curated"
        for classification in projected_candidates
    )


def test_actionable_role_does_not_invent_observed_cast_actionability() -> None:
    """RB-2: semantic role never manufactures per-log casts or cast cadence."""
    for spell_id, class_name, spec_name in (
        (191034, "Druid", "Balance"),
        (192106, "Shaman", "Enhancement"),
    ):
        rows = _aura_only_rows(spell_id, class_name, spec_name)
        assert rows
        assert all(row.projected[spell_id].role in ACTIONABLE_ROLES for row in rows)
        assert all(not row.projected[spell_id].signals.was_cast_by_player for row in rows)
        assert all(row.projected[spell_id].signals.cast_count == 0 for row in rows)
        assert all(row.projected[spell_id].signals.median_cast_interval_s is None for row in rows)
