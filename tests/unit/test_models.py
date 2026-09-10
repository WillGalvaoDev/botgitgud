from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from botgitgud.domain.models import (
    AbilityDamage,
    Cohort,
    CohortCriteria,
    FightRef,
    PlayerBuild,
    PlayerLog,
    RankingCandidate,
    SpellProfile,
)


def _fight() -> FightRef:
    return FightRef(
        report_code="ABCDEFGHIJKLMNOP",
        fight_id=1,
        encounter_id=3179,
        boss_name="Fallen-King Salhadaar",
        difficulty=5,
        duration_s=345.1,
        kill=True,
    )


def _build() -> PlayerBuild:
    return PlayerBuild(
        character_name="Zarad",
        server="Azralon",
        class_name="Warlock",
        spec_name="Demonology",
        role="dps",
        item_level=283.0,
        talent_hash=None,
        tier_pieces=None,
    )


def _criteria(**overrides: object) -> CohortCriteria:
    defaults: dict[str, object] = {
        "encounter_id": 3179,
        "difficulty": 5,
        "partition": 4,
        "class_name": "Warlock",
        "spec_name": "Demonology",
        "metric": "dps",
        "duration_min_s": 224.0,
        "duration_max_s": 466.0,
    }
    defaults.update(overrides)
    return CohortCriteria(**defaults)  # type: ignore[arg-type]


# -- immutability ---------------------------------------------------------------


def test_dataclasses_are_frozen() -> None:
    fight = _fight()
    with pytest.raises(AttributeError):
        fight.duration_s = 999.0  # type: ignore[misc]


def test_player_build_default_external_buffs_is_empty_frozenset() -> None:
    build = _build()
    assert build.external_buffs == frozenset()


def test_fight_ref_partition_defaults_to_none() -> None:
    """docs/desvios.md D-12(a): partition is nullable — resolved separately
    at ingestion time, not always known when a FightRef is first built.
    """
    fight = _fight()
    assert fight.partition is None
    with_partition = replace(fight, partition=4)
    assert with_partition.partition == 4


# -- PlayerLog composition --------------------------------------------------------


def test_player_log_builds_with_required_fields_and_defaults() -> None:
    log = PlayerLog(
        fight=_fight(),
        build=_build(),
        dps=108297.0,
        percentile=57.0,
        cast_timeline={104316: (1.3, 22.2, 43.1)},
    )
    assert log.active_time_pct is None
    assert log.damage_by_ability == {}
    assert log.uptimes == {}
    assert log.resource_waste == {}
    assert log.deaths == 0


def test_player_log_carries_ability_damage() -> None:
    dmg = AbilityDamage(spell_id=104316, total=1_000_000.0, hits=17, casts=17)
    log = PlayerLog(
        fight=_fight(),
        build=_build(),
        dps=100000.0,
        percentile=50.0,
        cast_timeline={},
        damage_by_ability={104316: dmg},
    )
    assert log.damage_by_ability[104316].total == 1_000_000.0


# -- CohortCriteria.cohort_id() ---------------------------------------------------


def test_cohort_id_is_deterministic_for_equal_criteria() -> None:
    a = _criteria()
    b = _criteria()
    assert a.cohort_id() == b.cohort_id()


def test_cohort_id_changes_when_any_field_changes() -> None:
    base = _criteria().cohort_id()
    for overrides in (
        {"encounter_id": 9999},
        {"difficulty": 3},
        {"partition": 1},
        {"class_name": "Mage"},
        {"spec_name": "Fire"},
        {"metric": "hps"},
        {"duration_min_s": 100.0},
        {"duration_max_s": 900.0},
        {"ilvl_min": 280.0},
        {"talent_cluster": "cluster-a"},
    ):
        assert _criteria(**overrides).cohort_id() != base, f"não mudou para {overrides}"


def test_cohort_id_is_a_16_char_hex_string() -> None:
    cid = _criteria().cohort_id()
    assert len(cid) == 16
    int(cid, 16)  # raises ValueError if not valid hex


# -- EC.2: matching_policy_version --------------------------------------------------


def test_matching_policy_version_defaults_to_v2() -> None:
    assert _criteria().matching_policy_version == "v2"


def test_v1_cohort_id_is_byte_identical_to_before_ec2() -> None:
    """Regressão CRÍTICA: `cohort_id()` de uma criteria v1 precisa ser
    EXATAMENTE o mesmo hash que este método produzia antes de
    `matching_policy_version` existir — senão todo cache/pool v1
    existente fica órfão silenciosamente. Valores capturados ANTES do
    EC.2 ser implementado, como oráculo de regressão.
    """
    criteria_a = CohortCriteria(
        encounter_id=3009,
        difficulty=5,
        partition=34,
        class_name="Warlock",
        spec_name="Demonology",
        metric="dps",
        duration_min_s=240.0,
        duration_max_s=360.0,
        ilvl_min=280.0,
        ilvl_max=None,
        talent_cluster="abc123",
        matching_policy_version="v1",
    )
    assert criteria_a.cohort_id() == "218423c4e7bcf385"

    criteria_b = CohortCriteria(
        encounter_id=3009,
        difficulty=5,
        partition=34,
        class_name="Warlock",
        spec_name="Demonology",
        metric="dps",
        duration_min_s=240.0,
        duration_max_s=360.0,
        matching_policy_version="v1",
    )
    assert criteria_b.cohort_id() == "2f5ae39c68eb3e11"


def test_v1_and_v2_produce_different_cohort_ids_for_the_same_fields() -> None:
    v1 = _criteria(matching_policy_version="v1")
    v2 = _criteria(matching_policy_version="v2")
    assert v1.cohort_id() != v2.cohort_id()


def test_v2_cohort_id_is_deterministic() -> None:
    a = _criteria(matching_policy_version="v2")
    b = _criteria(matching_policy_version="v2")
    assert a.cohort_id() == b.cohort_id()


def test_cohort_id_changes_when_matching_policy_version_changes() -> None:
    base = _criteria(matching_policy_version="v1").cohort_id()
    assert _criteria(matching_policy_version="v2").cohort_id() != base


def test_cohort_bundles_criteria_and_members() -> None:
    criteria = _criteria()
    log = PlayerLog(fight=_fight(), build=_build(), dps=100000.0, percentile=50.0, cast_timeline={})
    cohort = Cohort(
        cohort_id=criteria.cohort_id(),
        criteria=criteria,
        members=(log,),
        built_at=datetime.now(UTC),
    )
    assert cohort.cohort_id == criteria.cohort_id()
    assert len(cohort.members) == 1


# -- SpellProfile / RankingCandidate -------------------------------------------


def test_spell_profile_holds_its_reference_timings() -> None:
    spell = SpellProfile(
        spell_id=104316, presence=1.0, ref_times=(10.0, 130.0, 250.0), n_usages_median=4.0
    )
    assert spell.presence == 1.0
    assert spell.ref_times == (10.0, 130.0, 250.0)


def test_ranking_candidate_holds_report_identity() -> None:
    candidate = RankingCandidate(
        report_code="ABCDEFGHIJKLMNOP", fight_id=1, player_name="Zarad", duration_s=345.1
    )
    assert candidate.report_code == "ABCDEFGHIJKLMNOP"
    assert candidate.duration_s == 345.1
