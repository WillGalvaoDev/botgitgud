from __future__ import annotations

import inspect
from datetime import date
from pathlib import Path

import pytest
from real_corpus import require_real_corpus
from structlog.testing import capture_logs

from botgitgud.analysis import ability_classification
from botgitgud.analysis.ability_classification import (
    CORE_DAMAGE_SHARE,
    MIN_CASTS_FOR_CADENCE,
    OFFENSIVE_CD_INTERVAL_S,
    classify_abilities,
    classify_resolved_abilities,
)
from botgitgud.domain.ability_identity import AbilityIdentity, IdentitySource
from botgitgud.domain.ability_overrides import AbilityOverride, OverrideSource
from botgitgud.domain.ability_role import ACTIONABLE_ROLES, OFFENSIVE_ROLES, AbilityRole
from botgitgud.domain.canonical_ability import CanonicalAbility, FamilyProvenance
from botgitgud.domain.canonical_role import CanonicalAbilityRole, RoleSource
from botgitgud.domain.contextual_spec_roles import CONTEXTUAL_SPEC_ROLES
from botgitgud.domain.models import AbilityDamage, FightRef, PlayerBuild, PlayerLog
from botgitgud.domain.non_spec_effects import NON_SPEC_EFFECT_ROLES


def _player_log(
    *,
    damage: dict[int, float] | None = None,
    casts: dict[int, tuple[float, ...]] | None = None,
    uptimes: dict[int, float] | None = None,
    partition: int | None = 3,
) -> PlayerLog:
    return PlayerLog(
        fight=FightRef("report", 1, 1, "Boss", 5, 300.0, True, partition=partition),
        build=PlayerBuild("Player", None, "Mage", "Fire", "dps", None, None, None),
        dps=None,
        percentile=None,
        cast_timeline={} if casts is None else casts,
        damage_by_ability={
            spell_id: AbilityDamage(spell_id, total, 1, 0)
            for spell_id, total in ({} if damage is None else damage).items()
        },
        uptimes={} if uptimes is None else uptimes,
    )


def test_damage_cast_at_exact_share_boundary_is_core() -> None:
    result = classify_abilities(
        _player_log(damage={1: CORE_DAMAGE_SHARE, 2: 1.0 - CORE_DAMAGE_SHARE}, casts={1: (1.0,)})
    )[1]
    assert result.role is AbilityRole.CORE_DAMAGE
    assert result.rule == "rule_2_core"
    assert result.signals.damage_share == CORE_DAMAGE_SHARE


def test_damage_cast_below_share_boundary_is_secondary() -> None:
    result = classify_abilities(_player_log(damage={1: 2.0, 2: 98.0}, casts={1: (1.0,)}))[1]
    assert result.role is AbilityRole.SECONDARY_DAMAGE
    assert result.rule == "rule_2_secondary"


def test_damage_without_player_cast_is_indirect() -> None:
    result = classify_abilities(_player_log(damage={1: 10.0}))[1]
    assert result.role is AbilityRole.INDIRECT_DAMAGE
    assert result.rule == "rule_3_indirect"


def test_cast_cadence_at_exact_boundary_is_offensive_cooldown() -> None:
    casts = tuple(index * OFFENSIVE_CD_INTERVAL_S for index in range(MIN_CASTS_FOR_CADENCE))
    family = CanonicalAbility(
        "Mage", "Fire", "Cooldown", (1,), (2,), FamilyProvenance.DERIVED_NAME_MATCH, False
    )
    result = classify_abilities(_player_log(casts={1: casts}), families={1: family})[1]
    assert result.role is AbilityRole.OFFENSIVE_COOLDOWN
    assert result.rule == "rule_4_offensive_cd:canonical_family_damage"
    assert result.signals.median_cast_interval_s == OFFENSIVE_CD_INTERVAL_S


def test_cadence_without_independent_evidence_fails_closed() -> None:
    casts = (0.0, OFFENSIVE_CD_INTERVAL_S)
    result = classify_abilities(_player_log(casts={1: casts}))[1]
    assert result.role is AbilityRole.UNKNOWN
    assert result.rule == "rule_4_unknown_no_evidence"


@pytest.mark.parametrize(
    "spell_id",
    [1236616, 1236994, 1234768, 1295247, 6262, 452930, 1250508, 33702, 26297, 274738],
)
def test_curated_non_spec_effect_wins_over_observed_shape(spell_id: int) -> None:
    result = classify_abilities(
        _player_log(damage={spell_id: 10.0}, casts={spell_id: (0.0, 60.0)})
    )[spell_id]
    assert result.role is NON_SPEC_EFFECT_ROLES[spell_id]


@pytest.mark.parametrize("casts", [(10.0,), (0.0, OFFENSIVE_CD_INTERVAL_S - 0.1)])
def test_insufficient_or_short_cadence_is_unknown(casts: tuple[float, ...]) -> None:
    result = classify_abilities(_player_log(casts={1: casts}))[1]
    assert result.role is AbilityRole.UNKNOWN
    assert result.rule == "rule_6_unknown"


@pytest.mark.parametrize(
    ("spell_id", "expected"),
    [
        (10060, AbilityRole.EXTERNAL_OFFENSIVE),
        (395152, AbilityRole.EXTERNAL_OFFENSIVE),
        (29166, AbilityRole.EXTERNAL_NON_OFFENSIVE),
        (999999, AbilityRole.SELF_AURA_UNRESOLVED),
    ],
)
def test_pure_aura_roles(spell_id: int, expected: AbilityRole) -> None:
    assert classify_abilities(_player_log(uptimes={spell_id: 0.8}))[spell_id].role is expected


def test_zero_damage_is_semantically_no_damage() -> None:
    result = classify_abilities(_player_log(damage={1: 0.0}))[1]
    assert result.role is AbilityRole.UNKNOWN
    assert result.signals.has_damage is False
    assert result.signals.damage_share == 0.0


def test_override_for_partition_wins_and_is_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    override = AbilityOverride(
        1,
        AbilityRole.UTILITY,
        OverrideSource.CURATED,
        3,
        date(2026, 9, 4),
        "report/fight 1",
    )
    monkeypatch.setattr(ability_classification, "ABILITY_OVERRIDES", {1: override})
    with capture_logs() as logs:
        result = classify_abilities(_player_log(damage={1: 10.0}))[1]
    assert result.role is AbilityRole.UTILITY
    assert result.from_override is True
    assert result.rule == "rule_1_override"
    assert logs[0]["event"] == "ability_classification.override_applied"


def test_stale_override_is_ignored_and_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    override = AbilityOverride(
        1, AbilityRole.UTILITY, OverrideSource.DERIVED, 2, date(2026, 9, 4), "r/1"
    )
    monkeypatch.setattr(ability_classification, "ABILITY_OVERRIDES", {1: override})
    with capture_logs() as logs:
        result = classify_abilities(_player_log(damage={1: 10.0}, partition=3))[1]
    assert result.role is AbilityRole.INDIRECT_DAMAGE
    assert result.from_override is False
    assert logs[0]["event"] == "ability_classification.stale_override"


def test_signature_has_keyword_only_families_with_empty_default() -> None:
    signature = inspect.signature(classify_abilities)
    assert tuple(signature.parameters) == ("log", "families", "canonical_roles")
    assert "PlayerLog" in str(signature.parameters["log"].annotation)
    assert signature.parameters["families"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["canonical_roles"].kind is inspect.Parameter.KEYWORD_ONLY


def test_canonical_role_projects_uniformly_and_wins_over_shape() -> None:
    role = CanonicalAbilityRole(
        "Mage",
        "Fire",
        "Family",
        AbilityRole.UTILITY,
        RoleSource.CURATED,
        ("reviewed",),
        (1, 2),
    )
    log = _player_log(damage={1: 90.0, 2: 10.0}, casts={1: (1.0,), 2: (2.0,)})
    result = classify_abilities(log, canonical_roles={1: role, 2: role})
    assert result[1].role is result[2].role is AbilityRole.UTILITY
    assert result[1].rule == "rule_canonical_role:curated"


def test_non_spec_effect_wins_over_canonical_role() -> None:
    spell_id = 1236616
    role = CanonicalAbilityRole(
        "Mage",
        "Fire",
        "Family",
        AbilityRole.CORE_DAMAGE,
        RoleSource.DERIVED,
        ("share",),
        (spell_id,),
    )
    result = classify_abilities(
        _player_log(damage={spell_id: 10.0}), canonical_roles={spell_id: role}
    )
    assert result[spell_id].role is AbilityRole.CONSUMABLE


def test_classify_resolved_abilities_filters_only_unresolved_identity() -> None:
    log = _player_log(damage={1: 70.0, 2: 30.0}, casts={1: (1.0,), 2: (2.0,)})
    identities = {
        1: AbilityIdentity(1, "Resolved", IdentitySource.WCL_REPORT_MASTER_DATA, "resolved"),
        2: AbilityIdentity(2, "", IdentitySource.UNRESOLVED, "unresolved"),
    }

    filtered = classify_resolved_abilities(log, identities=identities)

    assert filtered == {1: classify_abilities(log)[1]}


def test_only_authorized_source_modules_consume_m5_modules() -> None:
    source_root = Path(__file__).resolve().parents[2] / "src"
    new_modules = {
        source_root / "botgitgud" / "domain" / "ability_role.py",
        source_root / "botgitgud" / "domain" / "ability_overrides.py",
        source_root / "botgitgud" / "analysis" / "ability_classification.py",
        source_root / "botgitgud" / "domain" / "non_spec_effects.py",
        source_root / "botgitgud" / "domain" / "canonical_role.py",
        source_root / "botgitgud" / "domain" / "curated_family_roles.py",
        source_root / "botgitgud" / "analysis" / "canonical_role.py",
        source_root / "botgitgud" / "domain" / "contextual_spec_roles.py",
    }
    # M9 consumes M5 deliberately to restrict proc analysis to self-offensive
    # auras. M7 consumes M5 because its eligibility is defined over AbilityRole.
    # M8 consumes M5 because feature availability is blocked by non-spec roles.
    # M12 wires M7/M8 into the pipeline and isolates external roles there.
    # Any additional consumer remains an unexpected coupling.
    authorized_consumers = {
        source_root / "botgitgud" / "analysis" / "proc_analysis.py",
        source_root / "botgitgud" / "analysis" / "core_ability_set.py",
        source_root / "botgitgud" / "analysis" / "feature_availability.py",
        source_root / "botgitgud" / "analysis" / "pipeline.py",
        # M1 consumes the established role classifier to apply its stricter
        # four-part coaching eligibility contract without redefining roles.
        source_root / "botgitgud" / "analysis" / "dps_gap.py",
    }
    needles = ("ability_role", "ability_overrides", "ability_classification")
    consumers = {
        path
        for path in source_root.rglob("*.py")
        if path not in new_modules
        and any(needle in path.read_text(encoding="utf-8") for needle in needles)
    }
    assert consumers == authorized_consumers


def test_default_unprojected_path_preserves_m5_spell_id_invariants_on_real_corpus() -> None:
    # This is deliberately the pre-M18/default path.  Production projection is
    # covered separately at the CanonicalAbility entity level.
    for log in require_real_corpus():
        first = classify_abilities(log)
        observed = set(log.damage_by_ability) | set(log.cast_timeline) | set(log.uptimes)
        assert set(first) == observed
        assert classify_abilities(log) == first
        assert all(
            first[spell_id].role in OFFENSIVE_ROLES
            for spell_id, damage in log.damage_by_ability.items()
            if damage.total > 0.0 and spell_id not in NON_SPEC_EFFECT_ROLES
        )
        assert all(
            classification.role not in ACTIONABLE_ROLES
            for spell_id, classification in first.items()
            if spell_id in log.uptimes
            and spell_id not in log.cast_timeline
            and not classification.signals.has_damage
        )


def test_nexcurse_live_log_calibration() -> None:
    matches = [
        log
        for log in require_real_corpus()
        if log.fight.report_code == "HkXRKf87WPpjT96w"
        and log.fight.fight_id == 12
        and log.build.character_name == "Nexcurse"
        and log.build.spec_name == "Demonology"
    ]
    assert len(matches) == 1
    player_log = matches[0]
    classifications = classify_abilities(player_log)
    damage_ids = {
        spell_id for spell_id, damage in player_log.damage_by_ability.items() if damage.total > 0.0
    }
    damage_and_cast = damage_ids & set(player_log.cast_timeline)
    indirect = damage_ids - set(player_log.cast_timeline)
    pure_auras = {
        spell_id
        for spell_id in player_log.uptimes
        if spell_id not in player_log.cast_timeline and spell_id not in damage_ids
    }

    assert len(damage_ids) == 37
    assert all(classifications[spell_id].role in OFFENSIVE_ROLES for spell_id in damage_ids)
    assert len(damage_and_cast) == 3
    assert all(
        classifications[spell_id].role in {AbilityRole.CORE_DAMAGE, AbilityRole.SECONDARY_DAMAGE}
        for spell_id in damage_and_cast
    )
    assert len(indirect) == 34
    assert all(
        classifications[spell_id].role is AbilityRole.INDIRECT_DAMAGE
        for spell_id in indirect
        - set(NON_SPEC_EFFECT_ROLES)
        - {key[2] for key in CONTEXTUAL_SPEC_ROLES if key[:2] == ("Warlock", "Demonology")}
    )
    assert indirect & set(NON_SPEC_EFFECT_ROLES)
    assert all(
        classifications[spell_id].role is NON_SPEC_EFFECT_ROLES[spell_id]
        for spell_id in indirect & set(NON_SPEC_EFFECT_ROLES)
    )
    contextual_ids = indirect & {
        key[2] for key in CONTEXTUAL_SPEC_ROLES if key[:2] == ("Warlock", "Demonology")
    }
    assert all(
        classifications[spell_id].role is CONTEXTUAL_SPEC_ROLES[("Warlock", "Demonology", spell_id)]
        for spell_id in contextual_ids
    )
    assert len(pure_auras) == 99
    assert all(
        classifications[spell_id].role
        in {
            AbilityRole.EXTERNAL_OFFENSIVE,
            AbilityRole.EXTERNAL_NON_OFFENSIVE,
            AbilityRole.SELF_AURA_UNRESOLVED,
        }
        for spell_id in pure_auras
    )
    assert all(classifications[spell_id].role not in ACTIONABLE_ROLES for spell_id in pure_auras)
