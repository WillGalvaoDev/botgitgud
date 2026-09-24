from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from botgitgud.analysis.dps_gap import analyze_dps_gap
from botgitgud.analysis.findings import TopPriorities, build_findings, select_top_priorities
from botgitgud.analysis.measurement import MetricStatus, account_damage, compare_damage
from botgitgud.analysis.performance_features import analyze_performance_features
from botgitgud.analysis.profile import build_cd_reference_profile
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
from botgitgud.ingest.damage_aggregation import RawDamageEvent, aggregate_damage_by_ability
from botgitgud.ingest.log_fetcher_aux import fetch_cast_timelines
from botgitgud.ingest.performance_fetch import _paginate_events
from botgitgud.report.coaching_answer import render_coaching_answer
from botgitgud.report.contract import ConfidenceSummary, ExecutionSection, ReportContract
from botgitgud.report.dps_gap_text import render_dps_gap_section
from botgitgud.report.text import ReportHeader


def _log(
    damage: float,
    *,
    fight_id: int = 1,
    duration: float = 300.0,
    spell_id: int = 1,
    hits: int = 1,
    casts: bool = True,
    origin: str = "PLAYER",
    damage_status: CollectionStatus = CollectionStatus.COMPLETE,
    casts_status: CollectionStatus = CollectionStatus.COMPLETE,
) -> PlayerLog:
    ability = AbilityDamage(spell_id, damage, hits, hits if casts else 0)
    return PlayerLog(
        fight=FightRef("ABCDEFGHIJKLMNOP", fight_id, 1, "Boss", 5, duration, True),
        build=PlayerBuild(f"P{fight_id}", "Realm", "Warlock", "Demonology", "dps", 280, None, 4),
        dps=damage / duration if duration > 0 else None,
        percentile=50,
        cast_timeline={spell_id: (1.0,)} if casts else {},
        damage_by_ability={spell_id: ability},
        damage_scope=DamageScopeVersion.WCL_TARGET_SCOPE_V1,
        measurement_provenance=MeasurementProvenance(
            damage_collection=CollectionProvenance(
                damage_status,
                () if damage_status is CollectionStatus.COMPLETE else ("INTERRUPTED",),
                0,
                duration * 1000,
            ),
            casts_collection=CollectionProvenance(
                casts_status,
                () if casts_status is CollectionStatus.COMPLETE else ("INTERRUPTED",),
                0,
                duration * 1000,
            ),
            damage_reconciliation_status="wcl_target_scope_v1",
            damage_table_total=damage,
            damage_event_mix_by_spell={spell_id: {f"{origin}:FALSE": EventMix(hits, damage)}},
        ),
    )


@pytest.fixture
def catalog(tmp_path: Path) -> SpellCatalog:
    value = SpellCatalog(tmp_path / "spells.json", blizzard=None)
    value.learn(1, "Example", "wcl")
    value.learn(2, "Other", "wcl")
    return value


@pytest.mark.parametrize(
    "payload",
    [{"data": {"reportData": None}}, {"data": {"reportData": {"report": None}}}],
)
def test_a14_nested_malformed_wcl_response_is_partial(payload: dict) -> None:
    events, provenance = _paginate_events(
        lambda *a, **k: payload,
        "",
        report_code="x",
        fight_id=1,
        start_time_ms=10,
        end_time_ms=20,
        op_name="test",
    )
    assert events == []
    assert provenance.status is CollectionStatus.PARTIAL
    assert provenance.reasons == ("INVALID_RESPONSE",)
    flat, phased, casts = fetch_cast_timelines(
        lambda *a, **k: payload,
        report_code="x",
        fight_id=1,
        player_id=1,
        start_time_ms=10,
        end_time_ms=20,
        intervals=(),
        include_provenance=True,
    )
    assert (flat, phased) == ({}, {})
    assert casts.status is CollectionStatus.PARTIAL


def test_a16_partial_player_never_produces_comparison() -> None:
    player = _log(100, damage_status=CollectionStatus.PARTIAL)
    result = compare_damage(player, tuple(_log(200, fight_id=i + 2) for i in range(15)))
    assert result.player.status is MetricStatus.PARTIAL
    assert result.total_delta_dps is None
    assert result.ability_delta_dps == {}
    assert result.support_delta_dps is None
    assert result.residual_dps is None


def test_a05_reference_order_is_canonical_and_exclusions_survive() -> None:
    player = _log(100)
    refs = tuple(_log(200, fight_id=i + 2) for i in range(15))
    partial = _log(200, fight_id=99, damage_status=CollectionStatus.PARTIAL)
    first = compare_damage(player, (*refs, partial))
    second = compare_damage(player, tuple(reversed((*refs, partial))))
    assert first == second
    assert len(first.excluded_references) == 1
    assert next(iter(first.excluded_references.values())) == "PARTIAL_DAMAGE_COLLECTION"


def test_a23_invalid_aspirational_member_is_excluded_without_crash(catalog: SpellCatalog) -> None:
    player = _log(100)
    refs = [_log(200, fight_id=i + 2) for i in range(15)]
    invalid = _log(200, fight_id=99, duration=0)
    report = analyze_dps_gap(
        player,
        refs,
        cohort_median_dps=None,
        catalog=catalog,
        buffs_relaxed=False,
        benchmark_reference=(invalid, *refs[:8]),
    )
    assert report.aspirational_comparison is not None
    assert report.aspirational_comparison.reference_n == 8
    assert len(report.aspirational_comparison.excluded_references) == 1
    assert report.benchmark_reference_dps == report.aspirational_comparison.reference_mean_net_dps


def test_a06_absent_player_mechanism_has_no_scalar_or_priority(catalog: SpellCatalog) -> None:
    player = _log(100, spell_id=2)
    refs = [_log(200, fight_id=i + 2) for i in range(15)]
    report = analyze_dps_gap(
        player, refs, cohort_median_dps=None, catalog=catalog, buffs_relaxed=False
    )
    ability = next(item for item in report.abilities if item.spell.spell_id == 1)
    assert ability.gross_dps_observation is not None
    assert ability.gross_dps_observation.status is MetricStatus.NOT_APPLICABLE
    assert ability.gross_dps_finding is None
    findings, relevance, _ = build_findings(
        dps_gap=report,
        n=15,
        relaxed_covariates=(),
        performance=None,
        player_damage_share={},
    )
    assert select_top_priorities(findings, relevance).level1 == ()


def test_a18_eight_metric_members_preserve_insufficient_scalar(catalog: SpellCatalog) -> None:
    player = _log(100)
    refs = [_log(200, fight_id=i + 2) for i in range(8)]
    ability = analyze_dps_gap(
        player, refs, cohort_median_dps=None, catalog=catalog, buffs_relaxed=False
    ).abilities[0]
    assert ability.gross_dps_finding is not None
    assert ability.gross_dps_finding.stats.n == 8
    assert ability.gross_dps_finding.grade == "insufficient"


def test_a18_partial_cast_references_are_excluded_from_cast_population() -> None:
    refs = [_log(100, fight_id=i + 2, casts_status=CollectionStatus.PARTIAL) for i in range(15)]
    profile, n = build_cd_reference_profile(refs, 300)
    assert profile == {}
    assert n == 0


def test_a16_zero_player_dps_retains_dps_closure_without_fake_percentages(
    catalog: SpellCatalog,
) -> None:
    player = _log(0, hits=0)
    refs = [_log(200, fight_id=i + 2) for i in range(15)]
    report = analyze_dps_gap(
        player, refs, cohort_median_dps=None, catalog=catalog, buffs_relaxed=False
    )
    assert report.total_delta_dps == pytest.approx(-200 / 300)
    assert report.total_delta_player_pp is None
    assert report.other_delta_dps == pytest.approx(-200 / 300)
    assert report.other_pct is None
    rendered = "\n".join(render_dps_gap_section(report))
    assert "-0.667 DPS" in rendered  # -200/300; do not round the measured deficit to -1.
    assert "0.0pp" not in rendered


def test_a08_pet_never_enters_top_priorities(catalog: SpellCatalog) -> None:
    player = _log(100, origin="PET", casts=False)
    refs = [_log(200, fight_id=i + 2, origin="PET", casts=False) for i in range(15)]
    report = analyze_dps_gap(
        player, refs, cohort_median_dps=None, catalog=catalog, buffs_relaxed=False
    )
    findings, relevance, _ = build_findings(
        dps_gap=report,
        n=15,
        relaxed_covariates=(),
        performance=None,
        player_damage_share={},
    )
    assert select_top_priorities(findings, relevance).level1 == ()


def test_a20_downtime_seconds_are_not_rendered_as_active_time_percent(
    catalog: SpellCatalog,
) -> None:
    player = replace(_log(100), active_time_pct=None, downtime_s=10)
    refs = [
        replace(_log(200, fight_id=i + 2), active_time_pct=None, downtime_s=0) for i in range(15)
    ]
    performance = analyze_performance_features(player, refs, catalog)
    report = analyze_dps_gap(
        player, refs, cohort_median_dps=None, catalog=catalog, buffs_relaxed=False
    )
    _findings, _relevance, execution = build_findings(
        dps_gap=report,
        n=15,
        relaxed_covariates=(),
        performance=performance,
        player_damage_share={},
    )
    assert all(item.category != "ACTIVE_TIME" for item in execution)
    contract = ReportContract(
        resultado=ReportHeader("P", "Boss", "Warlock", "Demonology", 15, 300, 300),
        setup=None,
        execucao=ExecutionSection((), performance, report),
        top_actions=TopPriorities(),
        confianca=ConfidenceSummary(15, 15, (), (), ()),
    )
    assert "1000%" not in render_coaching_answer(contract)


def test_a12_new_aggregation_does_not_calculate_targets_per_cast() -> None:
    _, targets = aggregate_damage_by_ability(
        [RawDamageEvent(1, 1, 99, 100.0)] * 10,
        {1: 10},
    )
    assert targets == {}


def test_a15_historical_v1_is_marked_as_legacy_reconciled() -> None:
    historical = replace(_log(100), measurement_provenance=None)
    accounting = account_damage(historical)
    assert accounting.status is MetricStatus.AVAILABLE
    assert accounting.reasons == ("LEGACY_RECONCILED_TOTAL",)
