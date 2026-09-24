"""M0 invariants migrated to M1: synthetic inputs, production calculators/renderers."""

from dataclasses import replace
from pathlib import Path
from statistics import median

import pytest

from botgitgud.analysis.dps_gap import DpsGapReport, _event_split_pair, analyze_dps_gap
from botgitgud.analysis.findings import TopPriorities, build_findings
from botgitgud.analysis.materiality import MaterialCandidate, collect_material_candidates
from botgitgud.analysis.measurement import account_damage
from botgitgud.analysis.performance_features import grade_scalar
from botgitgud.analysis.remediation import build_remediations
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
from botgitgud.phase4.experimental_dataset import build_features
from botgitgud.report.coaching_answer import render_coaching_answer
from botgitgud.report.contract import ConfidenceSummary, ExecutionSection, ReportContract
from botgitgud.report.text import ReportHeader


@pytest.fixture
def catalog(tmp_path: Path) -> SpellCatalog:
    result = SpellCatalog(tmp_path / "spells.json", blizzard=None)
    result.learn(1, "Example", "wcl")
    result.learn(2, "Second", "wcl")
    return result


def _log(
    counts: tuple[int, ...],
    per_use: tuple[float, ...],
    *,
    duration: float = 300.0,
    name: str = "Player",
) -> PlayerLog:
    damage = {
        spell_id: AbilityDamage(spell_id, count * unit_damage, count, count)
        for spell_id, (count, unit_damage) in enumerate(zip(counts, per_use, strict=True), 1)
    }
    return PlayerLog(
        fight=FightRef("ABCDEFGHIJKLMNOP", 1, 1, "Boss", 5, duration, True),
        build=PlayerBuild(name, "Realm", "Warlock", "Demonology", "dps", 280.0, None, 4),
        dps=sum(item.total for item in damage.values()) / duration,
        percentile=50.0,
        cast_timeline={},
        damage_by_ability=damage,
    )


def _references(log: PlayerLog, n: int = 15) -> list[PlayerLog]:
    # Unique players and pulls; these tests start AFTER cohort matching.
    return [
        replace(
            log,
            fight=replace(log.fight, fight_id=i + 2),
            build=replace(log.build, character_name=f"Reference{i}"),
        )
        for i in range(n)
    ]


def _eligible(log: PlayerLog) -> PlayerLog:
    if log.damage_scope is DamageScopeVersion.UNRECONCILED:
        return log
    gross = sum(a.total for a in log.damage_by_ability.values())
    return replace(
        log,
        cast_timeline={
            sid: tuple(log.fight.duration_s * (i + 1) / (item.casts + 1) for i in range(item.casts))
            for sid, item in log.damage_by_ability.items()
        },
        damage_scope=DamageScopeVersion.WCL_TARGET_SCOPE_V1,
        measurement_provenance=MeasurementProvenance(
            damage_collection=CollectionProvenance(
                CollectionStatus.COMPLETE, (), 0, log.fight.duration_s * 1000
            ),
            casts_collection=CollectionProvenance(
                CollectionStatus.COMPLETE, (), 0, log.fight.duration_s * 1000
            ),
            damage_reconciliation_status="wcl_target_scope_v1",
            damage_table_total=gross - log.support_subtracted_damage,
            damage_event_mix_by_spell={
                sid: {"PLAYER:FALSE": EventMix(item.hits, item.total)}
                for sid, item in log.damage_by_ability.items()
            },
        ),
    )


def _gap(player: PlayerLog, refs: list[PlayerLog], catalog: SpellCatalog) -> DpsGapReport:
    player = _eligible(player)
    refs = [_eligible(log) for log in refs]
    dps_values = [log.dps for log in refs if log.dps is not None]
    return analyze_dps_gap(
        player,
        refs,
        cohort_median_dps=median(dps_values) if dps_values else None,
        catalog=catalog,
        buffs_relaxed=False,
    )


def _candidates(report: DpsGapReport) -> tuple[MaterialCandidate, ...]:
    findings, relevance, execution = build_findings(
        dps_gap=report,
        n=15,
        relaxed_covariates=(),
        performance=None,
        player_damage_share={},
    )
    remediations = build_remediations(
        dps_gap=report,
        findings=findings,
        relevance_findings=relevance,
        execution_findings=execution,
    )
    return collect_material_candidates(
        dps_gap=report,
        remediations=remediations,
        performance=None,
    )


def test_m0_equal_duration_equal_output_control(catalog: SpellCatalog) -> None:
    player = _log((100,), (1000.0,))
    report = _gap(player, _references(player), catalog)
    assert report.gap_vs_reference_pct == 0
    assert report.abilities == ()
    assert report.other_pct == 0


def test_m0_duration_does_not_create_frequency_deficit(catalog: SpellCatalog) -> None:
    player = _log((100,), (1000.0,))
    ref = _log((110,), (1000.0,), duration=330.0)
    assert account_damage(player).gross_dps == pytest.approx(account_damage(ref).gross_dps)
    report = _gap(player, _references(ref), catalog)
    assert report.reference_n_quantitative == 15
    assert report.total_delta_dps == pytest.approx(0)
    assert report.comparison is not None
    assert report.comparison.ability_delta_dps[1] == pytest.approx(0)
    terms, reason = _event_split_pair(_eligible(player), _eligible(ref), 1)
    assert reason is None
    assert terms == pytest.approx((0, 0, 0))


@pytest.mark.parametrize("casts", [10, 20])
def test_m0_one_target_per_cast_is_one(casts: int) -> None:
    events = [RawDamageEvent(1, 1, 99, 100.0) for _ in range(casts)]
    _, targets = aggregate_damage_by_ability(events, {1: casts})
    assert targets == {}
    assert MeasurementProvenance().targets_per_cast_reasons == ("CAST_INSTANCE_LINK_UNAVAILABLE",)


def test_m0_target_proxy_reaches_experimental_feature() -> None:
    """Characterize propagation, not approval of the proxy's meaning."""
    _, targets = aggregate_damage_by_ability([RawDamageEvent(1, 1, 99, 100.0)] * 10, {1: 10})
    player = replace(_log((10,), (100.0,)), avg_targets_per_cast=targets)
    assert "c_mean_targets_per_cast" not in build_features(player, raid_size=20)


def test_m0_uniform_output_deficit_remains_visible_by_ability(catalog: SpellCatalog) -> None:
    player = _log((50, 50), (1000.0, 1000.0))
    report = _gap(player, _references(_log((100, 100), (1000.0, 1000.0))), catalog)
    # A review candidate is not proof of an executable action or recoverable gain.
    assert _candidates(report)
    assert all(c.remediation.basis.value == "OBSERVED_OUTPUT_DEFICIT" for c in _candidates(report))
    assert len(report.abilities) == 2
    for ability in report.abilities:
        assert ability.gross_dps_finding is not None
        assert ability.gross_dps_finding.grade == "red"
        assert ability.gross_dps_finding.stats.n == 15
        assert ability.player_ability_dps == pytest.approx(50000 / 300)
        assert ability.reference_mean_ability_dps == pytest.approx(100000 / 300)
    findings, _, _ = build_findings(
        dps_gap=report, n=15, relaxed_covariates=(), performance=None, player_damage_share={}
    )
    assert len(findings) == 2
    assert all(
        "DPS observado" in item.detail and not hasattr(item, "estimated_gain_pct")
        for item in findings
    )


def test_m0_share_rank_must_not_be_rendered_as_use_rank(catalog: SpellCatalog) -> None:
    # Player is below the median in uses, but above two references. Its
    # damage share is below ALL references because its other ability dominates.
    player = _log((50, 500), (1000.0, 1000.0))
    refs = _references(_log((100, 100), (1000.0, 1000.0)), n=13)
    for index, count in enumerate((40, 45), start=15):
        ref = _log((count, count), (1000.0, 1000.0), name=f"LowerUseReference{index}")
        refs.append(replace(ref, fight=replace(ref.fight, fight_id=index)))
    report = _gap(player, refs, catalog)
    contract = ReportContract(
        resultado=ReportHeader("Player", "Boss", "Warlock", "Demonology", 15, 300, 300),
        setup=None,
        execucao=ExecutionSection((), None, report),
        top_actions=TopPriorities(),
        confianca=ConfidenceSummary(15, 15, (), (), ()),
        material_priorities=_candidates(report),
    )
    output = render_coaching_answer(contract)
    assert "O uso de Example foi o mais baixo" not in output
    assert "Example" in output and "DPS observado" in output
    casts = report.metric_comparisons["player_casts_per_minute:1"]
    assert casts.finding is not None
    assert casts.finding.quantile == pytest.approx(2 / 15)
    assert casts.player.value == 10


def test_m0_separate_medians_do_not_explain_overall_gap(catalog: SpellCatalog) -> None:
    """D05 characterization: choice of replacement estimand remains B01."""
    player = _log((1,), (100.0,))
    refs = [
        ref
        for counts, unit in ((1, 100.0), (2, 2.0), (100, 1.0))
        for ref in _references(_log((counts,), (unit,)), n=5)
    ]
    refs = [
        replace(
            ref,
            fight=replace(ref.fight, fight_id=i + 2),
            build=replace(ref.build, character_name=f"Reference{i}"),
        )
        for i, ref in enumerate(refs)
    ]
    report = _gap(player, refs, catalog)
    assert report.comparison is not None
    assert report.comparison.reference_mean_net_dps == pytest.approx(68 / 300)
    assert report.total_delta_dps == pytest.approx(32 / 300)
    assert report.comparison.total_delta_player_pp == pytest.approx(32)
    assert report.comparison.gap_vs_reference_pct == pytest.approx(100 * 32 / 68)


def test_m0_support_adjusted_breakdown_closes(catalog: SpellCatalog) -> None:
    player = replace(
        _log((100,), (1000.0,)),
        damage_scope=DamageScopeVersion.WCL_TARGET_SCOPE_V1,
        support_subtracted_damage=10000.0,
        dps=90000.0 / 300,
    )
    report = _gap(player, [], catalog)
    # No reference: this tests the player's own damage accounting only.
    assert account_damage(player).net_damage == pytest.approx(90000.0)
    assert report.accounting_status == "NO_REFERENCES"
    assert report.total_delta_dps is None
    accounting = account_damage(player)
    assert accounting.net_damage is not None
    assert 100 * accounting.gross_damage_total / accounting.net_damage == pytest.approx(
        111.111111111
    )
    assert -100 * accounting.support_subtracted_damage / accounting.net_damage == pytest.approx(
        -11.111111111
    )
    assert (
        accounting.gross_damage_total - accounting.support_subtracted_damage
        == accounting.net_damage
    )


def test_m0_unreconciled_damage_is_unavailable(catalog: SpellCatalog) -> None:
    player = replace(_log((100,), (1000.0,)), damage_scope=DamageScopeVersion.UNRECONCILED)
    report = _gap(player, _references(player), catalog)
    assert not report.quantitative_damage_available
    assert report.abilities == ()


def test_m0_share_and_use_ranks_are_different_metrics() -> None:
    share = grade_scalar(50 / 550, [0.5] * 15, "higher_better")
    uses = grade_scalar(50, [100] * 13 + [40, 45], "higher_better")
    assert share.quantile == 0
    assert uses.quantile == pytest.approx(2 / 15)


def test_m1_positive_mean_reference_and_support_closure(catalog: SpellCatalog) -> None:
    def eligible(log: PlayerLog) -> PlayerLog:
        gross = sum(a.total for a in log.damage_by_ability.values())
        mix = {
            sid: {"PLAYER:FALSE": EventMix(int(a.hits), a.total)}
            for sid, a in log.damage_by_ability.items()
        }
        return replace(
            log,
            damage_scope=DamageScopeVersion.WCL_TARGET_SCOPE_V1,
            measurement_provenance=MeasurementProvenance(
                damage_collection=CollectionProvenance(
                    CollectionStatus.COMPLETE, (), 0, log.fight.duration_s * 1000
                ),
                casts_collection=CollectionProvenance(
                    CollectionStatus.COMPLETE, (), 0, log.fight.duration_s * 1000
                ),
                damage_reconciliation_status="wcl_target_scope_v1",
                damage_table_total=gross - log.support_subtracted_damage,
                damage_event_mix_by_spell=mix,
            ),
        )

    player = eligible(replace(_log((1,), (100.0,)), support_subtracted_damage=10.0))
    refs = [eligible(_log((1,), (value,))) for value in (100.0, 4.0, 100.0) for _ in range(5)]
    refs = [replace(ref, fight=replace(ref.fight, fight_id=i + 2)) for i, ref in enumerate(refs)]
    report = analyze_dps_gap(
        player,
        refs,
        cohort_median_dps=100 / 300,
        catalog=catalog,
        buffs_relaxed=False,
    )
    assert report.reference_n_quantitative == 15
    assert report.comparison is not None
    assert report.comparison.reference_mean_net_dps == pytest.approx(68 / 300)
    assert report.total_delta_dps == pytest.approx(90 / 300 - 68 / 300)
    assert report.comparison.residual_dps == pytest.approx(0.0)
