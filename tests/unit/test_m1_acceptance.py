from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from botgitgud.analysis.dps_gap import analyze_dps_gap
from botgitgud.analysis.measurement import MetricStatus, account_damage, compare_damage
from botgitgud.domain.damage_scope import DamageScopeVersion
from botgitgud.domain.models import (
    AbilityDamage,
    CollectionProvenance,
    CollectionStatus,
    EventMix,
    FightRef,
    MeasurementProvenance,
    PlayerBuild,
    PlayerLog,
)
from botgitgud.domain.spells import SpellCatalog
from botgitgud.ingest.performance_fetch import _paginate_events


def _log(
    damage: float,
    *,
    hits: int = 1,
    duration: float = 300.0,
    support: float = 0.0,
    origin: str = "PLAYER",
    periodicity: str = "FALSE",
    casts: bool = True,
    complete: CollectionStatus = CollectionStatus.COMPLETE,
    fight_id: int = 1,
) -> PlayerLog:
    ability = AbilityDamage(1, damage, hits, hits if casts else 0)
    provenance = MeasurementProvenance(
        damage_collection=CollectionProvenance(
            complete,
            () if complete is CollectionStatus.COMPLETE else ("INTERRUPTED",),
            0,
            duration * 1000,
        ),
        casts_collection=CollectionProvenance(CollectionStatus.COMPLETE, (), 0, duration * 1000),
        damage_reconciliation_status="wcl_target_scope_v1",
        damage_table_total=damage - support,
        damage_event_mix_by_spell={1: {f"{origin}:{periodicity}": EventMix(hits, damage)}},
    )
    return PlayerLog(
        fight=FightRef("ABCDEFGHIJKLMNOP", fight_id, 1, "Boss", 5, duration, True),
        build=PlayerBuild(f"P{fight_id}", "Realm", "Warlock", "Demonology", "dps", 280, None, 4),
        dps=(damage - support) / duration,
        percentile=50,
        cast_timeline={1: (1.0,)} if casts else {},
        damage_by_ability={1: ability},
        damage_scope=DamageScopeVersion.WCL_TARGET_SCOPE_V1,
        support_subtracted_damage=support,
        measurement_provenance=provenance,
    )


@pytest.fixture
def catalog(tmp_path: Path) -> SpellCatalog:
    result = SpellCatalog(tmp_path / "spells.json", blizzard=None)
    result.learn(1, "Example", "wcl")
    return result


def test_a01_duration_normalization_has_zero_frequency_delta(catalog: SpellCatalog) -> None:
    user = _log(100_000, hits=100, duration=300)
    refs = [_log(110_000, hits=110, duration=330, fight_id=i + 2) for i in range(15)]
    report = analyze_dps_gap(
        user, refs, cohort_median_dps=None, catalog=catalog, buffs_relaxed=False
    )
    assert report.total_delta_dps == pytest.approx(0)
    assert report.abilities == ()


def test_a03_a04_accounting_and_support_close() -> None:
    user = _log(100_000, support=10_000)
    accounting = account_damage(user)
    assert (accounting.net_damage, accounting.net_dps) == pytest.approx((90_000, 300))
    comparison = compare_damage(user, tuple(_log(100_000, fight_id=i + 2) for i in range(15)))
    assert comparison.ability_delta_dps[1] == pytest.approx(0)
    assert comparison.support_delta_dps == pytest.approx(-10_000 / 300)
    assert comparison.residual_dps == pytest.approx(0)


def test_a07_split_keeps_original_denominator_and_closes(catalog: SpellCatalog) -> None:
    user = _log(25_000, hits=50)
    valid = [_log(100_000, hits=100, fight_id=i + 2) for i in range(8)]
    invalid = [_log(100_000, hits=100, periodicity="UNKNOWN", fight_id=i + 20) for i in range(7)]
    report = analyze_dps_gap(
        user, valid + invalid, cohort_median_dps=None, catalog=catalog, buffs_relaxed=False
    )
    ability = report.abilities[0]
    assert ability.split_pair_count == 8
    # Independent values from §5; every term retains the original 1/15 weight.
    assert ability.volume_dps == pytest.approx(-800 / 9, rel=0, abs=1e-9)
    assert ability.per_event_dps == pytest.approx(-800 / 9, rel=0, abs=1e-9)
    assert ability.interaction_dps == pytest.approx(400 / 9, rel=0, abs=1e-9)
    assert ability.unclassified_dps == pytest.approx(-350 / 3, rel=0, abs=1e-9)
    assert ability.volume_dps_pct is not None
    assert ability.efficiency_dps_pct is not None
    assert ability.interaction_dps_pct is not None
    assert ability.unclassified_dps_pct is not None
    assert (
        ability.volume_dps_pct
        + ability.efficiency_dps_pct
        + ability.interaction_dps_pct
        + ability.unclassified_dps_pct
        == pytest.approx(ability.delta_dps_pct, rel=0, abs=1e-9)
    )


def test_a08_pet_is_ledger_only_and_not_coaching_eligible(catalog: SpellCatalog) -> None:
    user = _log(25_000, hits=50, origin="PET", casts=False)
    refs = [_log(100_000, hits=100, origin="PET", casts=False, fight_id=i + 2) for i in range(15)]
    ability = analyze_dps_gap(
        user, refs, cohort_median_dps=None, catalog=catalog, buffs_relaxed=False
    ).abilities[0]
    assert ability.unit_kind == "DAMAGE_EVENT"
    assert not ability.review_eligible


@pytest.mark.parametrize("cursor", [0, float("nan"), "bad"])
def test_a14_invalid_cursor_is_partial(cursor: object) -> None:
    response = {
        "data": {"reportData": {"report": {"events": {"data": [], "nextPageTimestamp": cursor}}}}
    }
    _, provenance = _paginate_events(
        lambda *a, **k: response,
        "",
        report_code="x",
        fight_id=1,
        start_time_ms=10,
        end_time_ms=20,
        op_name="test",
    )
    assert provenance.status is CollectionStatus.PARTIAL


def test_a16_invalid_beats_partial_and_blocks_comparison() -> None:
    provenance = _log(100).measurement_provenance
    assert provenance is not None
    bad = replace(
        _log(100),
        fight=replace(_log(100).fight, duration_s=0),
        measurement_provenance=replace(
            provenance,
            damage_collection=CollectionProvenance(CollectionStatus.PARTIAL, ("ERROR",)),
        ),
    )
    assert account_damage(bad).status is MetricStatus.INVALID
    assert compare_damage(bad, (_log(100, fight_id=2),)).total_delta_dps is None


def test_a22_arithmetic_mean_not_median() -> None:
    user = _log(100)
    refs = tuple(_log(value, fight_id=i + 2) for i, value in enumerate([100] * 14 + [10_000]))
    comparison = compare_damage(user, refs)
    assert comparison.reference_mean_net_dps == pytest.approx((14 * 100 + 10_000) / 15 / 300)
