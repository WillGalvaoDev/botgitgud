"""Permanent regressions for the independent review (user items 01-12).

Original evidence is archived in docs/reviews/m1-independent-2026-09-13.
Fixtures describe valid collection intervals unless a test corrupts them explicitly.
"""

from dataclasses import fields, replace
from pathlib import Path
from unittest.mock import patch

import pytest
from test_m1_astra_regressions import _log as original_log
from test_pipeline import _build_deps, _DispatchTransport, _req

import botgitgud.analysis.dps_gap as gap_module
from botgitgud.analysis.dps_gap import DpsGapReport, analyze_dps_gap
from botgitgud.analysis.findings import Finding, TopPriorities, build_findings
from botgitgud.analysis.materiality import collect_material_candidates
from botgitgud.analysis.measurement import MetricStatus, account_damage
from botgitgud.analysis.metric_observations import compare_metrics, observe
from botgitgud.analysis.performance_features import analyze_performance_features
from botgitgud.analysis.pipeline import _report_ability_sections, run_analysis
from botgitgud.analysis.remediation import build_remediations
from botgitgud.domain.models import CollectionProvenance, CollectionStatus, PlayerLog
from botgitgud.domain.spells import SpellCatalog
from botgitgud.ingest.log_fetcher_aux import fetch_cast_timelines
from botgitgud.ingest.parquet_codec import read_parquet_log, write_parquet_log
from botgitgud.ingest.performance_fetch import _paginate_events
from botgitgud.ingest.performance_parsing import parse_aura_uptimes
from botgitgud.report.coaching_answer import render_coaching_answer
from botgitgud.report.contract import ConfidenceSummary, ExecutionSection, ReportContract
from botgitgud.report.text import ReportHeader, _render_header, render_report


def log(damage: float = 100, **kwargs: object) -> PlayerLog:
    item = original_log(damage, **kwargs)  # type: ignore[arg-type]
    provenance = item.measurement_provenance
    assert provenance is not None
    return replace(
        item,
        measurement_provenance=replace(
            provenance,
            damage_collection=replace(
                provenance.damage_collection,
                requested_start_ms=0,
                requested_end_ms=item.fight.duration_s * 1000,
            ),
            casts_collection=replace(
                provenance.casts_collection,
                requested_start_ms=0,
                requested_end_ms=item.fight.duration_s * 1000,
            ),
            damage_reconciliation_status=item.damage_scope.value,
            damage_reconciliation_residual=0,
            player_actor_id=1,
        ),
    )


@pytest.fixture
def catalog(tmp_path: Path) -> SpellCatalog:
    cat = SpellCatalog(tmp_path / "spells.json", blizzard=None)
    cat.learn(1, "Example", "wcl")
    return cat


def gap(player: PlayerLog, refs: list[PlayerLog], catalog: SpellCatalog) -> DpsGapReport:
    return analyze_dps_gap(
        player, refs, cohort_median_dps=None, catalog=catalog, buffs_relaxed=False
    )


@pytest.mark.parametrize("timestamp", [-1.0, 301.0, float("nan"), float("inf")])
def test_u01_invalid_cast_cannot_authorize_coaching(
    timestamp: float, catalog: SpellCatalog
) -> None:
    player = replace(log(), cast_timeline={1: (timestamp,)})
    report = gap(player, [log(200, fight_id=i + 2) for i in range(15)], catalog)
    assert (
        report.metric_comparisons["player_casts_per_minute:1"].player.status is MetricStatus.INVALID
    )
    assert not report.abilities[0].review_eligible
    findings, _, _ = build_findings(
        dps_gap=report, n=15, relaxed_covariates=(), performance=None, player_damage_share={1: 1.0}
    )
    assert not findings
    remediations = build_remediations(
        dps_gap=report, findings=findings, relevance_findings=(), execution_findings=()
    )
    priorities = collect_material_candidates(
        dps_gap=report, remediations=remediations, performance=None
    )
    header = ReportHeader("Player", "Boss", "Warlock", "Demonology", 15, 300, 300)
    contract = ReportContract(
        resultado=header,
        setup=None,
        execucao=ExecutionSection((), None, report),
        top_actions=TopPriorities(),
        confianca=ConfidenceSummary(15, 15, (), (), ()),
        material_priorities=priorities,
    )
    assert not priorities
    assert "Revise a contribuição" not in render_coaching_answer(contract)
    assert "Revise a contribuição" not in render_report(header, (), dps_gap=report)


def test_u02_uptime_cannot_reintroduce_pet_coaching(catalog: SpellCatalog) -> None:
    player = replace(log(origin="PET", casts=False), uptimes={1: 0.1})
    refs = [
        replace(log(200, origin="PET", casts=False, fight_id=i + 2), uptimes={1: 0.9})
        for i in range(15)
    ]
    report = gap(player, refs, catalog)
    performance = analyze_performance_features(player, refs, catalog)
    assert performance.uptimes[0].finding.grade == "red"  # description is independent
    findings, relevance, execution = build_findings(
        dps_gap=report,
        n=15,
        relaxed_covariates=(),
        performance=performance,
        player_damage_share={1: 1.0},
    )
    assert not relevance  # also protects the legacy top-actions producer
    remediations = build_remediations(
        dps_gap=report,
        findings=findings,
        relevance_findings=relevance,
        execution_findings=execution,
    )
    assert not collect_material_candidates(
        dps_gap=report, remediations=remediations, performance=performance
    )


@pytest.mark.parametrize("stream", ["damage", "casts"])
@pytest.mark.parametrize("case", ["missing_cursor", "outside_interval"])
def test_u03_malformed_pages_are_not_complete(stream: str, case: str) -> None:
    content = (
        {"data": []}
        if case == "missing_cursor"
        else {
            "data": [{"type": "cast", "sourceID": 1, "abilityGameID": 1, "timestamp": 301000}],
            "nextPageTimestamp": None,
        }
    )
    payload = {"data": {"reportData": {"report": {"events": content}}}}
    if stream == "damage":
        _, quality = _paginate_events(
            lambda *a, **k: payload,
            "",
            op_name="test",
            report_code="x",
            fight_id=1,
            start_time_ms=0,
            end_time_ms=300000,
        )
    else:
        _, _, quality = fetch_cast_timelines(
            lambda *a, **k: payload,
            player_id=1,
            intervals=(),
            include_provenance=True,
            report_code="x",
            fight_id=1,
            start_time_ms=0,
            end_time_ms=300000,
        )
    assert quality.status is not CollectionStatus.COMPLETE
    assert quality.reasons


@pytest.mark.parametrize("case", ["inverted", "missing", "short", "reconciliation", "residual"])
def test_u04_corrupt_provenance_stays_blocked_on_disk(case: str, tmp_path: Path) -> None:
    player = log()
    provenance = player.measurement_provenance
    assert provenance is not None
    if case in {"inverted", "missing", "short"}:
        start, end = {"inverted": (300000, 0), "missing": (None, None), "short": (0, 1000)}[case]
        provenance = replace(
            provenance,
            damage_collection=CollectionProvenance(CollectionStatus.COMPLETE, (), start, end),
        )
    elif case == "reconciliation":
        provenance = replace(provenance, damage_reconciliation_status="unreconciled")
    else:
        provenance = replace(provenance, damage_reconciliation_residual=999)
    path = tmp_path / "corrupt.parquet"
    write_parquet_log(replace(player, measurement_provenance=provenance), path)
    accounting = account_damage(read_parquet_log(path))
    assert accounting.status is not MetricStatus.AVAILABLE
    assert accounting.reasons and accounting.net_dps is None


def test_u05_wcl_authority_is_exact() -> None:
    player = log(1e12)
    assert player.measurement_provenance is not None
    player = replace(
        player,
        measurement_provenance=replace(player.measurement_provenance, damage_table_total=1e12 - 1),
    )
    assert account_damage(player).status is MetricStatus.INVALID


@pytest.mark.parametrize("aura", [{"guid": 1}, {"guid": 1, "totalUptime": None}])
def test_u06_missing_uptime_is_not_zero(aura: dict, catalog: SpellCatalog) -> None:
    parsed = parse_aura_uptimes({"totalTime": 300000, "auras": [aura]})
    player = replace(log(), uptimes={a.spell_id: a.uptime_frac for a in parsed})
    refs = [replace(log(200, fight_id=i + 2), uptimes={1: 0.8}) for i in range(15)]
    comparison = compare_metrics(player, refs, catalog)["aura_uptime_fraction:1"]
    assert comparison.player.value is None and comparison.finding is None
    zero = parse_aura_uptimes({"totalTime": 300000, "auras": [{"guid": 1, "totalUptime": 0}]})
    assert zero[0].uptime_frac == 0


@pytest.mark.parametrize("case", ["legacy", "invalid"])
def test_u07_core_features_use_validated_availability(case: str, catalog: SpellCatalog) -> None:
    player = (
        replace(log(), measurement_provenance=None)
        if case == "legacy"
        else replace(log(), cast_timeline={1: (float("nan"),)}, uptimes={1: 2.0})
    )
    core, _, _, _ = _report_ability_sections(
        player, [log(200, fight_id=i + 2) for i in range(15)], catalog
    )
    assert core  # damage remains independently observable
    assert core[0].cast_count is None and core[0].casts_per_minute is None
    assert core[0].cast_timeline is None and core[0].uptime is None


@pytest.mark.parametrize("case", ["legacy_casts", "small_damage_R"])
def test_u08_header_uses_damage_population_and_public_floor(case: str, tmp_path: Path) -> None:
    refs = [log(200, fight_id=i + 2) for i in range(15)]
    if case == "legacy_casts":
        refs = [replace(r, measurement_provenance=None) for r in refs]
    else:
        refs = [
            log(
                200,
                fight_id=i + 2,
                damage_status=(CollectionStatus.COMPLETE if i < 7 else CollectionStatus.PARTIAL),
            )
            for i in range(15)
        ]
    deps = _build_deps(tmp_path, _DispatchTransport({}))
    try:
        with (
            patch.object(deps.fetcher, "fetch", return_value=replace(log(), dps=999)),
            patch.object(deps.store, "read_candidate_pool", return_value=[]),
            patch("botgitgud.analysis.pipeline.fetch_cohort_logs", return_value=refs),
            patch("botgitgud.analysis.pipeline.get_current_partition", return_value=1),
        ):
            result = run_analysis(_req(), deps)
        assert result.dps_gap is not None
        n = result.dps_gap.reference_n_quantitative
        assert result.header.reference_n == n
        assert result.conclusion is not None and result.conclusion.sample.matched_n == n
        assert result.header.damage_comparison is result.dps_gap.comparison
        assert result.conclusion.sample.damage_comparison is result.dps_gap.comparison
        assert result.conclusion.sample.source_identity == "measured_net_dps"
        assert result.conclusion.sample.unit == "DPS"
        assert result.header.matched_reference_n == 15
        assert result.header.positional_reference_n == (0 if case == "legacy_casts" else 15)
        text = "\n".join(_render_header(result.header))
        assert "WCL" in text
        if n < 8:
            assert result.header.cohort_median_dps is None
            assert "INSUFFICIENT_REFERENCES" in text
        else:
            assert "medido" in text
    finally:
        deps.store.close()


def test_u09_zero_damage_keeps_observed_event_metrics(catalog: SpellCatalog) -> None:
    player = log(0, hits=5)
    events = observe(player, 1, "damage_events_per_second", catalog)
    per_event = observe(player, 1, "damage_per_event", catalog)
    assert events.status is MetricStatus.AVAILABLE and events.value == 5 / 300
    assert per_event.status is MetricStatus.AVAILABLE and per_event.value == 0


def test_u10_derived_overflow_abstains(catalog: SpellCatalog) -> None:
    player = log(duration=1e-308)
    assert account_damage(player).status is MetricStatus.INVALID
    report = gap(player, [log(200, fight_id=i + 2) for i in range(15)], catalog)
    assert not report.quantitative_damage_available


def test_u11_wrong_split_cannot_be_hidden_in_unclassified(catalog: SpellCatalog) -> None:
    player = log(25000, hits=50)
    refs = [log(100000, hits=100, fight_id=i + 2) for i in range(15)]
    for i in range(8, 15):
        provenance = refs[i].measurement_provenance
        assert provenance is not None
        refs[i] = replace(
            refs[i], measurement_provenance=replace(provenance, damage_event_mix_by_spell={})
        )
    original = gap_module._event_split_pair

    def corrupt(*args: object, **kwargs: object) -> tuple:
        terms, reason = original(*args, **kwargs)  # type: ignore[arg-type]
        return (tuple(v * 15 / 8 for v in terms), reason) if terms is not None else (None, reason)

    with patch.object(gap_module, "_event_split_pair", side_effect=corrupt):
        report = gap(player, refs, catalog)
    assert not report.quantitative_damage_available
    assert report.accounting_status == "INVALID"
    assert report.total_delta_dps is None


def test_u12_active_contract_has_no_legacy_adapters(catalog: SpellCatalog) -> None:
    report = gap(log(), [log(200, fight_id=i + 2) for i in range(15)], catalog)
    retired = {
        "n_u",
        "d_u",
        "p_u",
        "n_r",
        "p_r",
        "d_r",
        "delta_d",
        "volume",
        "efficiency",
        "interaction",
        "cohort_share",
    }
    assert retired.isdisjoint(field.name for field in fields(report.abilities[0]))
    assert "estimated_gain_pct" not in {field.name for field in fields(Finding)}
    assert "gap_pct" not in {field.name for field in fields(DpsGapReport)}
    assert report.gap_vs_reference_pct == -50.0


def test_u01_u02_valid_entity_and_own_uptime_scalar_can_coach(catalog: SpellCatalog) -> None:
    player = replace(log(), uptimes={1: 0.1})
    refs = [replace(log(100, fight_id=i + 2), uptimes={1: 0.9}) for i in range(15)]
    report = gap(player, refs, catalog)
    assert not report.abilities  # no damage deficit; uptime has its own distribution
    performance = analyze_performance_features(player, refs, catalog)
    findings, relevance, execution = build_findings(
        dps_gap=report,
        n=15,
        relaxed_covariates=(),
        performance=performance,
        player_damage_share={1: 1.0},
    )
    assert not findings and len(relevance) == 1
    remediations = build_remediations(
        dps_gap=report,
        findings=findings,
        relevance_findings=relevance,
        execution_findings=execution,
    )
    priorities = collect_material_candidates(
        dps_gap=report, remediations=remediations, performance=performance
    )
    assert len(priorities) == 1


@pytest.mark.parametrize("stream", ["damage", "casts"])
def test_u03_explicit_empty_terminal_page_is_complete(stream: str) -> None:
    payload = {
        "data": {"reportData": {"report": {"events": {"data": [], "nextPageTimestamp": None}}}}
    }
    if stream == "damage":
        events, quality = _paginate_events(
            lambda *a, **k: payload,
            "",
            op_name="test",
            report_code="x",
            fight_id=1,
            start_time_ms=0,
            end_time_ms=300000,
        )
        assert events == []
    else:
        flat, phased, quality = fetch_cast_timelines(
            lambda *a, **k: payload,
            player_id=1,
            intervals=(),
            include_provenance=True,
            report_code="x",
            fight_id=1,
            start_time_ms=0,
            end_time_ms=300000,
        )
        assert flat == phased == {}
    assert quality.status is CollectionStatus.COMPLETE and not quality.reasons


def test_u08_header_cannot_publish_median_below_floor() -> None:
    header = ReportHeader(
        "Player",
        "Boss",
        "Warlock",
        "Demonology",
        7,
        300,
        300,
        cohort_median_dps=123456,
        damage_comparison_status="AVAILABLE",
    )
    rendered = "\n".join(_render_header(header))
    assert "123,456" not in rendered and "INSUFFICIENT_REFERENCES" in rendered


@pytest.mark.parametrize("case", ["sum_overflow", "percentage_overflow", "statistic_overflow"])
def test_u10_nonfinite_derived_quantities_abstain(case: str, catalog: SpellCatalog) -> None:
    from botgitgud.analysis.measurement import compare_damage
    from botgitgud.domain.models import AbilityDamage

    if case == "sum_overflow":
        player = replace(
            log(1e308),
            damage_by_ability={1: AbilityDamage(1, 1e308, 1, 0), 2: AbilityDamage(2, 1e308, 1, 0)},
        )
        result = account_damage(player)
        assert result.status is MetricStatus.INVALID and result.net_dps is None
    elif case == "percentage_overflow":
        result = compare_damage(log(1e-300), (log(1e308, fight_id=2),))
        assert result.status is MetricStatus.INVALID and result.total_delta_player_pp is None
        assert result.reasons == ("NONFINITE_DERIVED_COMPARISON",)
    else:
        player = log(1e308, duration=1)
        refs = [log(1e308, duration=1, fight_id=i + 2) for i in range(16)]
        result = compare_metrics(player, refs, catalog)["gross_ability_dps:1"]
        assert result.finding is None and result.reasons == ("NONFINITE_DERIVED_STATISTIC",)


def test_u11_small_closure_error_above_normative_tolerance_is_rejected(
    catalog: SpellCatalog,
) -> None:
    original = gap_module._event_split_pair

    def corrupt(player: PlayerLog, reference: PlayerLog, sid: int) -> tuple:
        terms, reason = original(player, reference, sid)
        assert terms is not None
        return (terms[0] + 2e-8, terms[1], terms[2]), reason

    with patch.object(gap_module, "_event_split_pair", side_effect=corrupt):
        report = gap(log(), [log(200, fight_id=i + 2) for i in range(15)], catalog)
    assert report.accounting_status == "INVALID"
    assert report.comparison_reasons == ("EVENT_SPLIT_CLOSURE_ERROR",)


@pytest.mark.parametrize("stream", ["damage", "casts"])
def test_u03_fetch_integrates_collection_abstention(stream: str, tmp_path: Path) -> None:
    from test_log_fetcher import _default_responses, _events_response, _make_fetcher, _meta_response

    responses = _default_responses()
    meta = _meta_response(actors=[{"id": 100, "name": "Target", "subType": "Boss"}])
    entry = meta["data"]["reportData"]["report"]["damageTable"]["data"]["entries"][0]
    entry.update(total=100, targets=[{"name": "Target", "type": "Boss"}])
    responses["meta"] = [meta]
    responses["damage_events"] = [
        _events_response(
            [
                {
                    "type": "damage",
                    "timestamp": 1500,
                    "sourceID": 6,
                    "targetID": 100,
                    "abilityGameID": 104316,
                    "amount": 100,
                    "tick": False,
                }
            ]
        )
    ]
    if stream == "damage":
        responses["damage_events"][0]["data"]["reportData"]["report"]["events"].pop(
            "nextPageTimestamp"
        )
    else:
        responses["events"] = [
            _events_response(
                [{"type": "cast", "timestamp": 100001, "sourceID": 6, "abilityGameID": 104316}]
            )
        ]
    fetcher, _, store = _make_fetcher(tmp_path, responses)
    try:
        player = fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")
        assert player.measurement_provenance is not None
        collection = (
            player.measurement_provenance.damage_collection
            if stream == "damage"
            else player.measurement_provenance.casts_collection
        )
        assert collection.status is CollectionStatus.PARTIAL and collection.reasons
        if stream == "casts":
            assert account_damage(player).status is MetricStatus.AVAILABLE
        else:
            assert account_damage(player).net_dps is None
    finally:
        store.close()


def test_u05_exact_authority_guard_survives_parquet(tmp_path: Path) -> None:
    player = log(1e12)
    assert player.measurement_provenance is not None
    player = replace(
        player,
        measurement_provenance=replace(player.measurement_provenance, damage_table_total=1e12 - 1),
    )
    path = tmp_path / "authority.parquet"
    write_parquet_log(player, path)
    restored = account_damage(read_parquet_log(path))
    assert restored.status is MetricStatus.INVALID and restored.net_dps is None
    assert restored.reconciliation_residual == 1


def test_u10_conclusion_abstains_from_nonfinite_statistics() -> None:
    from botgitgud.analysis.materiality import build_conclusion

    conclusion = build_conclusion(
        player_dps=1e308,
        cohort_dps_values=[1e308] * 16,
        percentile=50,
        matched_n=16,
        relaxed_covariates=(),
        material_count=0,
    )
    assert conclusion.standing is None
    assert conclusion.standing_reasons == ("NONFINITE_DERIVED_STATISTIC",)
    assert conclusion.sample.matched_n == 16


def test_u08_uptime_retains_own_population_and_public_floor(catalog: SpellCatalog) -> None:
    from botgitgud.report.performance_text import render_uptimes_section

    player = replace(log(), uptimes={1: 0.1})
    refs = [
        replace(log(100, fight_id=i + 2), uptimes={1: 0.9 if i < 7 else 2.0}) for i in range(15)
    ]
    report = gap(player, refs, catalog)
    performance = analyze_performance_features(player, refs, catalog)
    (uptime,) = performance.uptimes
    assert uptime.finding.stats.n == 7 and uptime.finding.grade == "insufficient"
    assert uptime.comparison is not None
    assert len(uptime.comparison.reference_ids) == 7
    assert len(uptime.comparison.excluded_references) == 8
    findings, relevance, _ = build_findings(
        dps_gap=report,
        n=15,
        relaxed_covariates=(),
        performance=performance,
        player_damage_share={1: 1.0},
    )
    assert not findings and not relevance
    assert "coorte mediana" not in "\n".join(render_uptimes_section(performance.uptimes))
