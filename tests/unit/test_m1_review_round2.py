from dataclasses import replace
from pathlib import Path

import pytest
from test_m0_methodology_contract import _candidates
from test_m1_astra_regressions import _log

from botgitgud.analysis.dps_gap import DpsGapReport, analyze_dps_gap
from botgitgud.analysis.findings import Finding, TopPriorities, build_findings, select_top_actions
from botgitgud.analysis.materiality import count_material
from botgitgud.analysis.performance_features import analyze_performance_features
from botgitgud.analysis.profile import build_cd_reference_profile
from botgitgud.analysis.remediation import build_remediations
from botgitgud.domain.models import EventMix, PlayerLog
from botgitgud.domain.spells import SpellCatalog
from botgitgud.ingest.event_validation import valid_event
from botgitgud.ingest.performance_fetch import _paginate_events
from botgitgud.report.coaching_answer import render_coaching_answer
from botgitgud.report.contract import ConfidenceSummary, ExecutionSection, ReportContract
from botgitgud.report.dps_gap_text import render_dps_gap_section
from botgitgud.report.text import ReportHeader
from botgitgud.report.top_actions_text import render_top_actions_section


@pytest.fixture
def catalog(tmp_path: Path) -> SpellCatalog:
    result = SpellCatalog(tmp_path / "spells.json", blizzard=None)
    result.learn(1, "Example", "wcl")
    return result


def gap(player: PlayerLog, refs: list[PlayerLog], catalog: SpellCatalog) -> DpsGapReport:
    return analyze_dps_gap(
        player, refs, cohort_median_dps=None, catalog=catalog, buffs_relaxed=False
    )


def test_ineligible_deficit_survives_delivery_association(catalog: SpellCatalog) -> None:
    report = gap(
        _log(100, origin="PET", casts=False),
        [_log(200, fight_id=i + 2, origin="PET", casts=False) for i in range(15)],
        catalog,
    )
    f, r, e = build_findings(
        dps_gap=report, n=15, relaxed_covariates=(), performance=None, player_damage_share={}
    )
    assert (
        build_remediations(dps_gap=report, findings=f, relevance_findings=r, execution_findings=e)
        == ()
    )
    assert (
        count_material(
            dps_gap=report, findings=f, relevance_findings=r, execution_findings=e, performance=None
        )
        == 0
    )


def test_positive_gross_zero_net_does_not_invent_percent(catalog: SpellCatalog) -> None:
    player = _log(100)
    assert player.measurement_provenance is not None
    player = replace(
        player,
        support_subtracted_damage=100,
        measurement_provenance=replace(player.measurement_provenance, damage_table_total=0),
    )
    report = gap(player, [_log(200, fight_id=i + 2) for i in range(15)], catalog)
    assert report.abilities[0].delta_dps_pct is None
    assert report.abilities[0].delta_ability_dps == pytest.approx(-1 / 3)
    assert "0.0pp" not in "\n".join(render_dps_gap_section(report))
    candidates = _candidates(report)
    assert len(candidates) == 1
    contract = ReportContract(
        resultado=ReportHeader("Player", "Boss", "Warlock", "Demonology", 15, 300, 300),
        setup=None,
        execucao=ExecutionSection((), None, report),
        top_actions=TopPriorities(),
        confianca=ConfidenceSummary(15, 15, (), (), ()),
        material_priorities=candidates,
    )
    message = render_coaching_answer(contract)
    assert "DPS observado" in message
    assert "0.0pp" not in message


@pytest.mark.parametrize("n", [0, 1, 7])
def test_public_comparison_requires_eight_references(catalog: SpellCatalog, n: int) -> None:
    report = gap(_log(100), [_log(200, fight_id=i + 2) for i in range(n)], catalog)
    rendered = "\n".join(render_dps_gap_section(report))
    assert ("NO_REFERENCES" if n == 0 else "INSUFFICIENT_REFERENCES") in rendered
    assert "pp" not in rendered


def test_missing_casts_do_not_pad_reference_with_zero() -> None:
    refs = [_log(100, fight_id=i + 2, casts=i < 7) for i in range(15)]
    profile, _ = build_cd_reference_profile(refs, 300)
    assert profile[1].n_usages_median == 1


def test_malformed_event_is_partial() -> None:
    payload = {
        "data": {"reportData": {"report": {"events": {"data": [None], "nextPageTimestamp": None}}}}
    }
    _, status = _paginate_events(
        lambda *a, **k: payload,
        "",
        report_code="x",
        fight_id=1,
        start_time_ms=0,
        end_time_ms=10,
        op_name="regression",
    )
    assert status.status.value == "PARTIAL"


def test_nonfinite_hits_abstain_instead_of_crashing(catalog: SpellCatalog) -> None:
    player = _log(100)
    player = replace(
        player, damage_by_ability={1: replace(player.damage_by_ability[1], hits=float("nan"))}
    )
    report = gap(player, [_log(200, fight_id=i + 2) for i in range(15)], catalog)
    assert report.abilities[0].split_pair_count == 0
    assert report.abilities[0].split_pair_reasons


def test_all_buckets_must_reconcile(catalog: SpellCatalog) -> None:
    player = _log(100)
    assert player.measurement_provenance is not None
    player = replace(
        player,
        measurement_provenance=replace(
            player.measurement_provenance,
            damage_event_mix_by_spell={
                1: {"PLAYER:FALSE": EventMix(1, 100), "UNKNOWN:UNKNOWN": EventMix(0, 999)}
            },
        ),
    )
    report = gap(player, [_log(200, fight_id=i + 2) for i in range(15)], catalog)
    assert report.abilities[0].split_pair_count == 0


def test_contradictory_reference_identity_is_rejected(catalog: SpellCatalog) -> None:
    refs = [_log(v, fight_id=2) for v in [100, 200, 300] * 5]
    first = gap(_log(50), refs, catalog)
    second = gap(_log(50), list(reversed(refs)), catalog)
    assert first == second
    assert first.reference_n_quantitative == 0
    assert first.comparison is not None
    assert "CONFLICTING_REFERENCE_IDENTITY" in first.comparison.excluded_references.values()


def test_each_metric_has_its_own_population_and_typed_absence(catalog: SpellCatalog) -> None:
    player = replace(_log(100), uptimes={1: 0.0})
    refs = [
        replace(_log(200, fight_id=i + 2, casts=i < 7), uptimes={1: 0.5} if i < 9 else {})
        for i in range(15)
    ]
    report = gap(player, refs, catalog)
    metrics = report.metric_comparisons
    assert len(metrics["player_casts_per_minute:1"].reference_ids) == 7
    assert len(metrics["aura_uptime_fraction:1"].reference_ids) == 9
    assert metrics["aura_uptime_fraction:1"].player.value == 0
    assert len(metrics["gross_ability_dps:1"].reference_ids) == 15
    assert metrics["damage_per_event:1"].player.value == 100
    absent = gap(replace(player, cast_timeline={}), refs, catalog)
    assert absent.metric_comparisons["player_casts_per_minute:1"].player.value is None
    assert absent.metric_comparisons["player_casts_per_minute:1"].player.reasons


@pytest.mark.parametrize(
    "event",
    [
        {"type": "damage", "sourceID": 1, "abilityGameID": 1, "amount": "bad", "timestamp": 1},
        {"type": "damage", "sourceID": 1, "abilityGameID": 1, "amount": -1, "timestamp": 1},
        {"type": "cast", "sourceID": 1, "abilityGameID": 1, "timestamp": float("nan")},
    ],
)
def test_malformed_event_fields_are_partial(event: dict[str, object]) -> None:
    payload = {
        "data": {"reportData": {"report": {"events": {"data": [event], "nextPageTimestamp": None}}}}
    }
    _, status = _paginate_events(
        lambda *a, **k: payload,
        "",
        report_code="x",
        fight_id=1,
        start_time_ms=0,
        end_time_ms=10,
        op_name="regression",
    )
    assert status.status.value == "PARTIAL"


def test_uptime_consumer_rejects_conflicting_identity(catalog: SpellCatalog) -> None:
    player = replace(_log(100), uptimes={1: 0.2})
    refs = [replace(_log(200, fight_id=2), uptimes={1: value}) for value in [0.5, 0.8, 0.9] * 5]
    performance = analyze_performance_features(player, refs, catalog)
    assert not performance.uptimes


def test_invalid_cast_timestamp_is_excluded_from_profile() -> None:
    refs = [
        replace(_log(100, fight_id=i + 2), cast_timeline={1: (float("nan"),)}) for i in range(15)
    ]
    profile, _ = build_cd_reference_profile(refs, 300)
    assert not profile


@pytest.mark.parametrize("damage", [float("nan"), float("inf"), -100.0])
def test_invalid_bucket_damage_rejected_by_domain(damage: float) -> None:
    with pytest.raises(ValueError, match="invalid event mix"):
        EventMix(1, damage)


def test_boolean_cursor_is_not_a_timestamp() -> None:
    payload = {
        "data": {"reportData": {"report": {"events": {"data": [], "nextPageTimestamp": True}}}}
    }
    _, status = _paginate_events(
        lambda *a, **k: payload,
        "",
        report_code="x",
        fight_id=1,
        start_time_ms=0,
        end_time_ms=1,
        op_name="regression",
    )
    assert status.status.value == "PARTIAL"


def test_recorded_wcl_negative_ability_id_is_valid() -> None:
    # WCL cassette contains damage abilityGameID=-32; identity is signed, not amount.
    assert valid_event(
        {"type": "damage", "sourceID": 37, "targetID": 157, "abilityGameID": -32, "amount": 3186}
    )


@pytest.mark.parametrize(
    "event",
    [
        {},
        {"type": []},
        {"type": {}},
        {
            "type": "damage",
            "sourceID": 1,
            "targetID": 2,
            "abilityGameID": 3,
            "amount": 4,
            "absorbed": "bad",
        },
        {
            "type": "damage",
            "sourceID": 1,
            "targetID": 2,
            "abilityGameID": 3,
            "amount": 4,
            "absorbed": -1,
        },
        {
            "type": "damage",
            "sourceID": 1,
            "targetID": 2,
            "abilityGameID": 3,
            "amount": 4,
            "subtractsFromSupportedActor": True,
            "supportID": "bad",
        },
    ],
)
def test_malformed_parser_operands_fail_closed(event: dict[str, object]) -> None:
    assert not valid_event(event)


def test_retired_gain_is_never_a_scoring_or_rendering_authority() -> None:
    legacy = Finding("ABILITY_GAP", "Example", "old", "alta")
    assert legacy.score is None
    assert select_top_actions([legacy]) == []
    text = "\n".join(render_top_actions_section(TopPriorities(level1=(legacy,))))
    assert "99" not in text
    assert "indisponível" in text


def test_positive_ledger_row_and_reference_n_are_explicit(catalog: SpellCatalog) -> None:
    report = gap(_log(200), [_log(100, fight_id=i + 2) for i in range(15)], catalog)
    rendered = "\n".join(render_dps_gap_section(report))
    assert "N=15" in rendered
    assert "déficit de saída" not in rendered
