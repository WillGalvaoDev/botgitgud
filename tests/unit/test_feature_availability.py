from __future__ import annotations

from collections import Counter
from dataclasses import replace
from functools import cache
from pathlib import Path

import pytest
from real_corpus import require_real_corpus

from botgitgud.analysis.ability_classification import classify_abilities
from botgitgud.analysis.canonical_role import (
    derive_canonical_roles,
    index_canonical_roles_for_spec,
)
from botgitgud.analysis.core_ability_set import build_core_ability_sets
from botgitgud.analysis.feature_availability import (
    FeatureAvailability,
    FeatureBlockReason,
    FeatureKind,
    evaluate_feature_availability,
)
from botgitgud.domain.ability_identity import AbilityIdentity, IdentitySource
from botgitgud.domain.ability_role import AbilityRole
from botgitgud.domain.canonical_ability import (
    CanonicalAbility,
    FamilyProvenance,
    build_ability_families,
    index_families_for_spec,
    observe_specs,
)
from botgitgud.domain.models import (
    AbilityDamage,
    FightRef,
    PlayerBuild,
    PlayerLog,
)
from botgitgud.domain.spells import SpellCatalog


@cache
def _real_pipeline() -> tuple[
    tuple[PlayerLog, ...],
    dict[int, AbilityIdentity],
    tuple[CanonicalAbility, ...],
]:
    logs = tuple(require_real_corpus())
    catalog = SpellCatalog(
        Path(__file__).resolve().parents[2] / "data" / "spells.json", blizzard=None
    )
    observed_ids = set().union(
        *(set(log.cast_timeline) | set(log.damage_by_ability) | set(log.uptimes) for log in logs)
    )
    identities = {spell_id: catalog.identity(spell_id) for spell_id in observed_ids}
    families = build_ability_families(observe_specs(logs), identities=identities)
    return logs, identities, families


def _ability() -> CanonicalAbility:
    return CanonicalAbility(
        class_name="Shaman",
        spec_name="Enhancement",
        resolved_name="Lightning Shield",
        cast_ids=(1,),
        damage_ids=(2,),
        provenance=FamilyProvenance.DERIVED_NAME_MATCH,
        multi_cast=False,
    )


def _log(
    *,
    casts: tuple[float, ...] = (),
    damage: float | None = None,
    uptime: bool = False,
    duration_s: float = 60.0,
) -> PlayerLog:
    return PlayerLog(
        fight=FightRef("report", 1, 1, "boss", 5, duration_s, True),
        build=PlayerBuild("player", None, "Shaman", "Enhancement", "dps", None, None, None),
        dps=None,
        percentile=None,
        cast_timeline={1: casts} if casts else {},
        damage_by_ability=(
            {2: AbilityDamage(spell_id=2, total=damage, hits=1, casts=0)}
            if damage is not None
            else {}
        ),
        uptimes={1: 0.0} if uptime else {},
    )


def _identities(*, resolved: bool = True) -> dict[int, AbilityIdentity]:
    if not resolved:
        return {}
    return {
        spell_id: AbilityIdentity(spell_id, "Lightning Shield", IdentitySource.CURATED, "resolved")
        for spell_id in (1, 2)
    }


def _evaluate(
    log: PlayerLog, role: AbilityRole | None = AbilityRole.SECONDARY_DAMAGE
) -> tuple[FeatureAvailability, ...]:
    return evaluate_feature_availability(_ability(), role, log, identities=_identities())


def _by_kind(results: tuple[FeatureAvailability, ...]) -> dict[FeatureKind, FeatureAvailability]:
    return {result.kind: result for result in results}


@pytest.mark.parametrize(
    ("available", "reasons"),
    [(True, (FeatureBlockReason.NO_SIGNAL,)), (False, ())],
)
def test_feature_availability_rejects_inconsistent_invariant(
    available: bool, reasons: tuple[FeatureBlockReason, ...]
) -> None:
    with pytest.raises(ValueError):
        FeatureAvailability(FeatureKind.CAST_COUNT, available, reasons)


def test_each_feature_uses_only_its_observed_signal() -> None:
    results = _by_kind(_evaluate(_log(casts=(1.0,), damage=10.0, uptime=True)))
    assert all(result.available and result.reasons == () for result in results.values())

    results = _by_kind(_evaluate(_log()))
    assert all(
        not result.available and result.reasons == (FeatureBlockReason.NO_SIGNAL,)
        for result in results.values()
    )


def test_casts_per_minute_also_requires_positive_duration() -> None:
    results = _by_kind(_evaluate(_log(casts=(1.0,), duration_s=0)))
    assert results[FeatureKind.CAST_COUNT].available
    assert results[FeatureKind.CAST_TIMELINE].available
    assert results[FeatureKind.CASTS_PER_MINUTE].reasons == (FeatureBlockReason.NO_DURATION,)


@pytest.mark.parametrize(
    ("log", "available"),
    [
        (_log(casts=(1.0,), damage=10.0, uptime=True), set(FeatureKind)),
        (_log(damage=10.0), {FeatureKind.DAMAGE_SHARE}),
        (_log(uptime=True), {FeatureKind.UPTIME}),
    ],
)
def test_lightning_shield_real_signal_combinations(
    log: PlayerLog, available: set[FeatureKind]
) -> None:
    results = _evaluate(log)
    assert {result.kind for result in results if result.available} == available
    for result in results:
        if result.kind not in available:
            assert result.reasons == (FeatureBlockReason.NO_SIGNAL,)


@pytest.mark.parametrize(
    "role",
    [
        None,
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
    ],
)
def test_non_spec_roles_block_every_feature(role: AbilityRole | None) -> None:
    results = _evaluate(_log(casts=(1.0,), damage=10.0, uptime=True), role)
    assert all(result.reasons == (FeatureBlockReason.NON_SPEC_ROLE,) for result in results)


def test_all_applicable_reasons_are_sorted() -> None:
    results = evaluate_feature_availability(
        _ability(), AbilityRole.UNKNOWN, _log(duration_s=0), identities={}
    )
    assert _by_kind(results)[FeatureKind.CASTS_PER_MINUTE].reasons == tuple(
        sorted(FeatureBlockReason, key=lambda reason: reason.value)
    )


def test_any_resolved_member_prevents_unresolved_identity_block() -> None:
    one_identity = {1: _identities()[1]}
    results = evaluate_feature_availability(
        _ability(), AbilityRole.SECONDARY_DAMAGE, _log(casts=(1.0,)), identities=one_identity
    )
    assert _by_kind(results)[FeatureKind.CAST_COUNT].available


def test_unresolved_identity_blocks_every_feature() -> None:
    results = evaluate_feature_availability(
        _ability(),
        AbilityRole.SECONDARY_DAMAGE,
        _log(casts=(1.0,), damage=10.0, uptime=True),
        identities={},
    )
    assert all(result.reasons == (FeatureBlockReason.UNRESOLVED_IDENTITY,) for result in results)


def test_empty_cast_entry_is_absent_but_zero_uptime_is_observed() -> None:
    log = replace(_log(uptime=True), cast_timeline={1: ()})
    results = _by_kind(_evaluate(log))
    assert results[FeatureKind.CAST_COUNT].reasons == (FeatureBlockReason.NO_SIGNAL,)
    assert results[FeatureKind.UPTIME].available


def test_output_is_complete_sorted_and_independent_of_mapping_order() -> None:
    first = _evaluate(_log(casts=(2.0, 1.0), damage=10.0, uptime=True))
    permuted = _evaluate(
        replace(
            _log(casts=(1.0, 2.0), damage=10.0, uptime=True),
            cast_timeline={99: (4.0,), 1: (1.0, 2.0)},
            uptimes={99: 1.0, 1: 0.0},
        )
    )
    assert first == permuted
    assert tuple(result.kind for result in first) == tuple(
        sorted(FeatureKind, key=lambda kind: kind.value)
    )


def test_lightning_shield_real_logs_keep_role_but_only_expose_observed_features() -> None:
    logs, identities, families = _real_pipeline()
    roles = derive_canonical_roles(logs, families, identities=identities)
    (ability,) = (
        item
        for item in families
        if (item.class_name, item.spec_name, item.resolved_name)
        == ("Shaman", "Enhancement", "Lightning Shield")
    )
    (role,) = (
        item.role
        for item in roles
        if (item.class_name, item.spec_name, item.canonical_name)
        == ("Shaman", "Enhancement", "Lightning Shield")
    )
    assert role is AbilityRole.SECONDARY_DAMAGE

    combinations: dict[tuple[bool, bool, bool], int] = {}
    for log in logs:
        if (log.build.class_name, log.build.spec_name) != ("Shaman", "Enhancement"):
            continue
        decisions = _by_kind(
            evaluate_feature_availability(ability, role, log, identities=identities)
        )
        observed = (
            decisions[FeatureKind.CAST_COUNT].available,
            decisions[FeatureKind.DAMAGE_SHARE].available,
            decisions[FeatureKind.UPTIME].available,
        )
        if any(observed):
            combinations[observed] = combinations.get(observed, 0) + 1
        assert decisions[FeatureKind.CAST_TIMELINE].available == observed[0]
        assert decisions[FeatureKind.CASTS_PER_MINUTE].available == observed[0]

    assert combinations == {
        (True, True, True): 1,
        (False, True, False): 6,
        (False, False, True): 4,
    }


def test_no_feature_is_invented_across_all_measured_core_entity_log_pairs() -> None:
    logs, identities, families = _real_pipeline()
    roles = derive_canonical_roles(logs, families, identities=identities)
    core_sets = build_core_ability_sets(families, roles, logs, identities=identities)
    logs_by_spec: dict[tuple[str, str], list[PlayerLog]] = {}
    for log in logs:
        logs_by_spec.setdefault((log.build.class_name, log.build.spec_name), []).append(log)

    pair_count = 0
    for core_set in core_sets:
        for item in core_set.abilities:
            members = item.ability.cast_ids + item.ability.damage_ids
            for log in logs_by_spec[(core_set.class_name, core_set.spec_name)]:
                pair_count += 1
                decisions = _by_kind(
                    evaluate_feature_availability(
                        item.ability, item.role, log, identities=identities
                    )
                )
                has_cast = any(log.cast_timeline.get(member, ()) for member in members)
                has_damage = any(
                    member in log.damage_by_ability and log.damage_by_ability[member].total > 0
                    for member in members
                )
                has_uptime = any(member in log.uptimes for member in members)
                expected = {
                    FeatureKind.DAMAGE_SHARE: has_damage,
                    FeatureKind.CAST_COUNT: has_cast,
                    FeatureKind.CASTS_PER_MINUTE: has_cast and log.fight.duration_s > 0,
                    FeatureKind.CAST_TIMELINE: has_cast,
                    FeatureKind.UPTIME: has_uptime,
                }
                for kind, available in expected.items():
                    assert decisions[kind].available is available
                    if not available and not (
                        kind is FeatureKind.CASTS_PER_MINUTE and log.fight.duration_s <= 0
                    ):
                        assert FeatureBlockReason.NO_SIGNAL in decisions[kind].reasons

    assert pair_count == 11_878


def test_all_unknown_entities_fail_closed_through_the_consumer_path() -> None:
    logs, identities, families = _real_pipeline()
    roles = derive_canonical_roles(logs, families, identities=identities)
    core_sets = build_core_ability_sets(families, roles, logs, identities=identities)
    core_names = {
        (result.class_name, result.spec_name, item.ability.resolved_name)
        for result in core_sets
        for item in result.abilities
    }
    specs = sorted({(log.build.class_name, log.build.spec_name) for log in logs})
    residuals: list[CanonicalAbility] = []

    for spec in specs:
        spec_logs = [log for log in logs if (log.build.class_name, log.build.spec_name) == spec]
        family_index = dict(index_families_for_spec(families, *spec))
        role_index = dict(index_canonical_roles_for_spec(roles, *spec))
        observed_by_name: dict[str, set[int]] = {}
        counts: dict[str, Counter[AbilityRole]] = {}
        ever_cast = set().union(*(set(log.cast_timeline) for log in spec_logs))
        for log in spec_logs:
            projected = classify_abilities(log, families=family_index, canonical_roles=role_index)
            for spell_id, classification in projected.items():
                identity = identities.get(spell_id)
                if identity is None or identity.resolution_status != "resolved":
                    continue
                family = family_index.get(spell_id)
                name = family.resolved_name if family is not None else identity.resolved_name
                observed_by_name.setdefault(name, set()).add(spell_id)
                counts.setdefault(name, Counter())[classification.role] += 1

        families_by_name = {
            family.resolved_name: family
            for family in families
            if (family.class_name, family.spec_name) == spec
        }
        for name, member_ids in observed_by_name.items():
            role = min(counts[name], key=lambda item: (-counts[name][item], item.value))
            if role is not AbilityRole.UNKNOWN:
                continue
            family = families_by_name.get(name)
            if family is None:
                cast_ids = tuple(sorted(member_ids & ever_cast))
                damage_ids = tuple(sorted(member_ids - ever_cast))
                family = CanonicalAbility(
                    spec[0],
                    spec[1],
                    name,
                    cast_ids,
                    damage_ids,
                    FamilyProvenance.DERIVED_NAME_MATCH,
                    len(cast_ids) > 1,
                )
            residuals.append(family)
            assert (*spec, name) not in core_names
            for log in spec_logs:
                decisions = evaluate_feature_availability(family, role, log, identities=identities)
                assert all(not decision.available for decision in decisions)
                assert all(
                    FeatureBlockReason.NON_SPEC_ROLE in decision.reasons for decision in decisions
                )

    assert residuals
