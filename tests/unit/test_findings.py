from __future__ import annotations

from dataclasses import fields

import pytest

from botgitgud.analysis.dps_gap import AbilityGap, DpsGapReport
from botgitgud.analysis.findings import (
    ExecutionFinding,
    Finding,
    RelevanceFinding,
    TopPriorities,
    build_findings,
    compute_confidence,
    select_top_actions,
    select_top_priorities,
)
from botgitgud.analysis.grading import Grade, QuantileStats
from botgitgud.analysis.performance_features import (
    Direction,
    PerformanceFindings,
    ScalarFinding,
    UptimeFinding,
    WasteFinding,
    grade_scalar,
)
from botgitgud.domain.spells import SpellInfo

_STATS = QuantileStats(30, 0.1, 0.2, 0.5, 0.8, 0.9)


def _scalar(
    grade: Grade,
    *,
    quantile: float | None = 0.5,
    user_value: float = 0.0,
    direction: Direction = "higher_better",
) -> ScalarFinding:
    return ScalarFinding(
        grade=grade,
        quantile=quantile,
        user_value=user_value,
        stats=_STATS,
        ci90=(0.4, 0.6),
        direction=direction,
    )


def _ability_gap(**overrides: object) -> AbilityGap:
    defaults: dict[str, object] = {
        "spell": SpellInfo(spell_id=1, name="Chaos Strike", source="wcl"),
        "n_u": 8.0,
        "d_u": 800.0,
        "p_u": 100.0,
        "n_r": 10.0,
        "d_r": 1000.0,
        "p_r": 100.0,
        "delta_d": -200.0,
        "volume": -200.0,
        "efficiency": 0.0,
        "interaction": 0.0,
        "delta_dps_pct": -6.3,
        "volume_dps_pct": -6.3,
        "efficiency_dps_pct": 0.0,
        "diagnosis": "usos_perdidos_excedentes",
        "confidence": "alta",
        "unit_kind": "CAST",
    }
    defaults.update(overrides)
    return AbilityGap(**defaults)  # type: ignore[arg-type]


def _finding(**overrides: object) -> Finding:
    defaults: dict[str, object] = {
        "kind": "ABILITY_GAP",
        "title": "x",
        "detail": "y",
        "estimated_gain_pct": 5.0,
        "confidence": "alta",
    }
    defaults.update(overrides)
    return Finding(**defaults)  # type: ignore[arg-type]


def _performance(*quantiles: float | None) -> PerformanceFindings:
    scalar = ScalarFinding(
        grade="red",
        quantile=0.0,
        user_value=0.0,
        stats=QuantileStats(30, 0.1, 0.2, 0.5, 0.8, 0.9),
        ci90=(0.4, 0.6),
        direction="higher_better",
    )
    uptimes = tuple(
        UptimeFinding(
            spell=SpellInfo(spell_id=i + 1, name=f"Spell {i + 1}", source="wcl"),
            finding=ScalarFinding(
                grade="red",
                quantile=q,
                user_value=0.0,
                stats=scalar.stats,
                ci90=scalar.ci90,
                direction="higher_better",
            ),
        )
        for i, q in enumerate(quantiles)
    )
    return PerformanceFindings(None, scalar, scalar, uptimes, ())


# -- compute_confidence ----------------------------------------------------------


def test_confidence_alta_needs_all_three_conditions() -> None:
    assert compute_confidence(n=30, any_covariate_relaxed=False, survives_bh=True) == "alta"


def test_confidence_baixa_when_n_below_15() -> None:
    assert compute_confidence(n=14, any_covariate_relaxed=False) == "baixa"


def test_confidence_baixa_when_feature_incomplete_regardless_of_n() -> None:
    assert (
        compute_confidence(n=100, any_covariate_relaxed=False, feature_incomplete=True) == "baixa"
    )


def test_confidence_alta_at_60_even_when_covariate_relaxed() -> None:
    assert compute_confidence(n=60, any_covariate_relaxed=True) == "alta"


def test_confidence_media_when_n_between_15_and_30() -> None:
    assert compute_confidence(n=20, any_covariate_relaxed=False) == "média"


def test_confidence_five_normative_bands() -> None:
    assert compute_confidence(n=7, any_covariate_relaxed=False) == "baixa"
    assert compute_confidence(n=8, any_covariate_relaxed=False) == "baixa"
    assert compute_confidence(n=15, any_covariate_relaxed=False) == "média"
    assert compute_confidence(n=30, any_covariate_relaxed=False) == "alta"
    assert compute_confidence(n=30, any_covariate_relaxed=True) == "média"
    assert compute_confidence(n=60, any_covariate_relaxed=True) == "alta"


def test_confidence_media_when_bh_not_survived() -> None:
    assert compute_confidence(n=100, any_covariate_relaxed=False, survives_bh=False) == "baixa"


# -- build_findings: only ABILITY_GAP gets a real gain (D-31; EC.4 removed BUILD) --


def test_build_findings_skips_ability_gaps_already_ahead_of_cohort() -> None:
    dps_gap = DpsGapReport(
        player_dps=1000.0,
        cohort_median_dps=1200.0,
        gap_pct=-1 / 6,
        duration_s=300.0,
        abilities=(_ability_gap(delta_dps_pct=2.0),),  # ahead of cohort — nothing to gain
        other_pct=0.0,
        n_other=0,
        measured_dps=1000.0,
    )
    findings, _, _ = build_findings(
        dps_gap=dps_gap, n=30, relaxed_covariates=(), performance=None, player_damage_share={}
    )
    assert not any(f.kind == "ABILITY_GAP" for f in findings)


def test_build_findings_ability_gap_gain_is_the_negated_delta() -> None:
    dps_gap = DpsGapReport(
        player_dps=1000.0,
        cohort_median_dps=1200.0,
        gap_pct=-1 / 6,
        duration_s=300.0,
        abilities=(_ability_gap(delta_dps_pct=-6.3),),
        other_pct=0.0,
        n_other=0,
        measured_dps=1000.0,
    )
    findings, _, _ = build_findings(
        dps_gap=dps_gap, n=30, relaxed_covariates=(), performance=None, player_damage_share={}
    )
    ability_finding = next(f for f in findings if f.kind == "ABILITY_GAP")
    assert ability_finding.estimated_gain_pct == 6.3
    assert ability_finding.detail == "Gap de -6.3pp do seu dano medido nesta habilidade."


def test_build_findings_ability_gap_low_confidence_propagates() -> None:
    dps_gap = DpsGapReport(
        player_dps=1000.0,
        cohort_median_dps=1200.0,
        gap_pct=-1 / 6,
        duration_s=300.0,
        abilities=(_ability_gap(delta_dps_pct=-6.3, confidence="baixa"),),
        other_pct=0.0,
        n_other=0,
        measured_dps=1000.0,
    )
    findings, _, _ = build_findings(
        dps_gap=dps_gap, n=100, relaxed_covariates=(), performance=None, player_damage_share={}
    )
    ability_finding = next(f for f in findings if f.kind == "ABILITY_GAP")
    assert ability_finding.confidence == "baixa"


# -- select_top_actions: score, ordering, gating -------------------------------


def test_low_confidence_larger_gain_ranks_below_high_confidence_smaller_gain() -> None:
    low = _finding(title="low", estimated_gain_pct=5.0, confidence="baixa")
    high = _finding(title="high", estimated_gain_pct=3.0, confidence="alta")
    top = select_top_actions([low, high])
    assert [f.title for f in top] == ["high", "low"]


def test_findings_without_gain_never_enter_top_actions() -> None:
    no_gain = _finding(title="no-gain", estimated_gain_pct=None)
    with_gain = _finding(title="with-gain", estimated_gain_pct=1.0)
    top = select_top_actions([no_gain, with_gain])
    assert [f.title for f in top] == ["with-gain"]


def test_top_actions_never_exceeds_three() -> None:
    findings = [_finding(title=str(i), estimated_gain_pct=float(i)) for i in range(10)]
    top = select_top_actions(findings)
    assert len(top) == 3
    assert [f.title for f in top] == ["9", "8", "7"]


def test_top_actions_empty_when_nothing_clears_the_gate() -> None:
    findings = [_finding(estimated_gain_pct=None) for _ in range(5)]
    assert select_top_actions(findings) == []


def test_relevance_finding_has_no_estimated_gain_field() -> None:
    finding = RelevanceFinding("UPTIME", "x", "y", 0.2, 0.8, "alta")
    assert not hasattr(finding, "estimated_gain_pct")
    assert "estimated_gain_pct" not in {field.name for field in fields(RelevanceFinding)}
    assert finding.score == pytest.approx(0.16)


def test_relevance_uses_measured_share_and_applies_floor_and_bh() -> None:
    report = DpsGapReport(1000, 1200, None, 300, (), 0, 0)
    _, relevance, _ = build_findings(
        dps_gap=report,
        n=30,
        relaxed_covariates=(),
        performance=_performance(0.01, 0.30, 0.08),
        player_damage_share={1: 0.25, 2: 0.50, 3: 0.10},
    )
    assert len(relevance) == 1
    assert relevance[0].offensive_relevance == 0.25
    assert relevance[0].evidence["spell_id"] == 1


def test_relevance_discards_missing_quantile_and_missing_damage_share() -> None:
    report = DpsGapReport(1000, 1200, None, 300, (), 0, 0)
    _, relevance, _ = build_findings(
        dps_gap=report,
        n=30,
        relaxed_covariates=(),
        performance=_performance(None, 0.01),
        player_damage_share={1: 0.5},
    )
    assert relevance == []


def test_two_levels_respect_order_limit_and_do_not_pad() -> None:
    level1 = [_finding(title="measured", estimated_gain_pct=2.0)]
    level2 = [RelevanceFinding("UPTIME", "relevant", "d", 0.3, 0.9, "alta")]
    top = select_top_priorities(level1, level2)
    assert isinstance(top, TopPriorities)
    assert [item.title for item in top.level1] == ["measured"]
    assert [item.title for item in top.level2] == ["relevant"]
    assert len(top.level1) + len(top.level2) == 2


def test_quantitative_damage_unavailable_still_allows_measured_relevance() -> None:
    report = DpsGapReport(
        1000,
        1200,
        None,
        300,
        (),
        0,
        0,
        measured_dps=0.0,
        quantitative_damage_available=False,
    )
    level1, level2, _ = build_findings(
        dps_gap=report,
        n=30,
        relaxed_covariates=(),
        performance=_performance(0.01),
        player_damage_share={1: 1.0},
    )
    assert level1 == []
    assert len(level2) == 1


def test_zero_damage_share_is_not_established_relevance() -> None:
    report = DpsGapReport(1000, 1200, None, 300, (), 0, 0)
    _, relevance, _ = build_findings(
        dps_gap=report,
        n=30,
        relaxed_covariates=(),
        performance=_performance(0.01),
        player_damage_share={1: 0.0},
    )
    assert relevance == []


def test_empty_damage_share_fails_closed_for_both_levels_when_quantitative_unavailable() -> None:
    report = DpsGapReport(
        1000,
        1200,
        None,
        300,
        (_ability_gap(),),
        0,
        0,
        quantitative_damage_available=False,
    )
    level1, level2, _ = build_findings(
        dps_gap=report,
        n=30,
        relaxed_covariates=(),
        performance=_performance(0.01),
        player_damage_share={},
    )
    assert level1 == []
    assert level2 == []


# -- M27: execution findings (DEATH/ACTIVE_TIME/WASTE) connected from  ---------
# -- performance_features.py's already-graded ScalarFindings — RB-1/1a/2/3 ----

_NEUTRAL_REPORT = DpsGapReport(1000, 1200, None, 300, (), 0, 0)


def test_execution_findings_nonvacuous_over_zarad_death() -> None:
    """Test 1 (the one that matters), M27-spec.md's own "Regressão" section:
    grade Zarad's REAL measured death count (deaths=1) against the REAL
    corpus distribution the spec reports (1,274 logs; 187 with deaths > 0
    — 166 with exactly 1, 21 with exactly 2), using the product's own
    `grade_scalar` — not an asserted-by-fiat red. Empirically this grades
    red (bad_q ~0.082 < 0.10, verified below rather than assumed), and
    downtime is graded `insufficient` here exactly as it is for real in
    tests/golden's frozen Zarad snapshot (matched cohort n=10 < 15) — so
    this test isolates DEATH as the sole source of non-vacuity, matching
    the spec's own claim ("a morte observada produz um finding"). A test
    that would also pass against the pre-M27 build_findings (which never
    constructs DEATH/ACTIVE_TIME/WASTE at all, and returns a 2-tuple) is
    worthless — it cannot even be called this way against that code.
    """
    corpus_deaths = [0.0] * 1087 + [1.0] * 166 + [2.0] * 21
    assert len(corpus_deaths) == 1274
    deaths_finding = grade_scalar(1.0, corpus_deaths, "lower_better")
    assert deaths_finding.grade == "red"  # empirical, not assumed

    downtime_finding = grade_scalar(8.216, [], "lower_better")
    assert downtime_finding.grade == "insufficient"

    performance = PerformanceFindings(
        active_time=None,
        deaths=deaths_finding,
        downtime=downtime_finding,
        uptimes=(),
        resource_waste=(),
    )

    _, _, execution = build_findings(
        dps_gap=_NEUTRAL_REPORT,
        n=30,
        relaxed_covariates=(),
        performance=performance,
        player_damage_share={},
    )

    assert execution != []  # non-vacuity
    assert [f.category for f in execution] == ["DEATH"]
    assert execution[0].finding is deaths_finding


def test_green_and_insufficient_never_enter_execution_candidates() -> None:
    """Test 2: `green` (fine) and `insufficient` (no comparison base, §7)
    are graded, but neither is material — RB-2 excludes both.
    """
    performance = PerformanceFindings(
        active_time=_scalar("green", direction="higher_better"),
        deaths=_scalar("insufficient", quantile=None, direction="lower_better"),
        downtime=_scalar("insufficient", quantile=None, direction="lower_better"),
        uptimes=(),
        resource_waste=(
            WasteFinding("Fragmentos de Alma", _scalar("green", direction="lower_better")),
        ),
    )

    _, _, execution = build_findings(
        dps_gap=_NEUTRAL_REPORT,
        n=30,
        relaxed_covariates=(),
        performance=performance,
        player_damage_share={},
    )

    assert execution == []


def test_waste_emits_one_finding_per_resource_type_not_an_aggregate() -> None:
    """Test 3: WASTE is per-resource, never collapsed into one aggregate
    finding — a resource-mana warlock and a resource-fury warrior are
    different remediations even when both waste resource.
    """
    performance = PerformanceFindings(
        active_time=None,
        deaths=_scalar("insufficient", quantile=None, direction="lower_better"),
        downtime=_scalar("insufficient", quantile=None, direction="lower_better"),
        uptimes=(),
        resource_waste=(
            WasteFinding("Fragmentos de Alma", _scalar("red", direction="lower_better")),
            WasteFinding("Fúria", _scalar("yellow", direction="lower_better")),
            WasteFinding("Energia", _scalar("green", direction="lower_better")),  # excluded, RB-2
        ),
    )

    _, _, execution = build_findings(
        dps_gap=_NEUTRAL_REPORT,
        n=30,
        relaxed_covariates=(),
        performance=performance,
        player_damage_share={},
    )

    waste = [f for f in execution if f.category == "WASTE"]
    assert {f.subject for f in waste} == {"Fragmentos de Alma", "Fúria"}
    assert len(waste) == 2  # one per material resource type


def test_execution_finding_carries_scalar_fields_unchanged() -> None:
    """Test 4: no transformation — the ExecutionFinding's ScalarFinding is
    the exact object performance_features.py produced (grade/quantile/
    user_value/direction all identical, never recomputed or rescaled).
    """
    death_scalar = _scalar("red", quantile=0.05, user_value=2.0, direction="lower_better")
    performance = PerformanceFindings(
        active_time=None,
        deaths=death_scalar,
        downtime=_scalar("insufficient", quantile=None, direction="lower_better"),
        uptimes=(),
        resource_waste=(),
    )

    _, _, execution = build_findings(
        dps_gap=_NEUTRAL_REPORT,
        n=30,
        relaxed_covariates=(),
        performance=performance,
        player_damage_share={},
    )

    death = next(f for f in execution if f.category == "DEATH")
    assert death.finding is death_scalar
    assert death.finding.grade == "red"
    assert death.finding.quantile == 0.05
    assert death.finding.user_value == 2.0
    assert death.finding.direction == "lower_better"


def test_active_time_emits_at_most_one_candidate_and_death_stays_separate() -> None:
    """Test 5 / RB-1a: even when BOTH active_time and downtime are
    independently material, ACTIVE_TIME still emits exactly one candidate
    — the direct measure (active_time_pct) is preferred — and DEATH is a
    separate category, unaffected by that choice.
    """
    active = _scalar("red", direction="higher_better")
    downtime = _scalar("yellow", direction="lower_better")
    deaths = _scalar("red", direction="lower_better")
    performance = PerformanceFindings(
        active_time=active, deaths=deaths, downtime=downtime, uptimes=(), resource_waste=()
    )

    _, _, execution = build_findings(
        dps_gap=_NEUTRAL_REPORT,
        n=30,
        relaxed_covariates=(),
        performance=performance,
        player_damage_share={},
    )

    active_time_candidates = [f for f in execution if f.category == "ACTIVE_TIME"]
    assert len(active_time_candidates) == 1
    assert active_time_candidates[0].finding is active

    death_candidates = [f for f in execution if f.category == "DEATH"]
    assert len(death_candidates) == 1
    assert death_candidates[0].finding is deaths
    assert {f.category for f in execution} == {"ACTIVE_TIME", "DEATH"}


def test_active_time_falls_back_to_downtime_when_active_time_pct_unavailable() -> None:
    """RB-1a, the other branch: when this log never computed
    active_time_pct (`performance.active_time is None`), downtime backs
    the single ACTIVE_TIME candidate instead — still exactly one, never
    zero just because the direct measure is missing.
    """
    downtime = _scalar("yellow", direction="lower_better")
    performance = PerformanceFindings(
        active_time=None,
        deaths=_scalar("insufficient", quantile=None, direction="lower_better"),
        downtime=downtime,
        uptimes=(),
        resource_waste=(),
    )

    _, _, execution = build_findings(
        dps_gap=_NEUTRAL_REPORT,
        n=30,
        relaxed_covariates=(),
        performance=performance,
        player_damage_share={},
    )

    assert [f.category for f in execution] == ["ACTIVE_TIME"]
    assert execution[0].finding is downtime


def test_execution_findings_empty_when_performance_is_none() -> None:
    _, _, execution = build_findings(
        dps_gap=_NEUTRAL_REPORT,
        n=30,
        relaxed_covariates=(),
        performance=None,
        player_damage_share={},
    )
    assert execution == []


def test_select_top_priorities_unaffected_by_material_execution_evidence() -> None:
    """Test 6 / RB-3 pinning: the exact same dps_gap/uptime input produces
    byte-identical TopPriorities regardless of whether `performance` ALSO
    carries material DEATH/WASTE evidence. `select_top_priorities` and its
    inputs (`Finding`, `RelevanceFinding`) are untouched by this unit —
    only `build_findings`' NEW third return value differs between the two
    calls below.
    """
    dps_gap = DpsGapReport(
        1000,
        1200,
        None,
        300,
        (_ability_gap(delta_dps_pct=-6.3),),
        0,
        0,
        measured_dps=1000.0,
    )
    share = {1: 0.25, 2: 0.50, 3: 0.10}
    without_waste = _performance(0.01, 0.30, 0.08)
    with_waste = PerformanceFindings(
        active_time=without_waste.active_time,
        deaths=without_waste.deaths,
        downtime=without_waste.downtime,
        uptimes=without_waste.uptimes,
        resource_waste=(
            WasteFinding("Fragmentos de Alma", _scalar("red", direction="lower_better")),
        ),
    )

    findings_a, relevance_a, execution_a = build_findings(
        dps_gap=dps_gap,
        n=30,
        relaxed_covariates=(),
        performance=without_waste,
        player_damage_share=share,
    )
    findings_b, relevance_b, execution_b = build_findings(
        dps_gap=dps_gap,
        n=30,
        relaxed_covariates=(),
        performance=with_waste,
        player_damage_share=share,
    )

    assert execution_a != execution_b  # sanity: the new WASTE evidence really is there

    top_a = select_top_priorities(findings_a, relevance_a)
    top_b = select_top_priorities(findings_b, relevance_b)
    assert top_a == top_b


def test_execution_finding_has_no_estimated_gain_or_relevance_or_cross_kind_fields() -> None:
    """Test 7: ExecutionFinding carries only what RB-1 allows — category,
    the backing ScalarFinding, and an optional subject. No
    `estimated_gain_pct`, no `offensive_relevance`, no cross-kind score.
    """
    field_names = {f.name for f in fields(ExecutionFinding)}
    assert field_names == {"category", "finding", "subject"}
    assert "estimated_gain_pct" not in field_names
    assert "offensive_relevance" not in field_names

    finding = ExecutionFinding(category="DEATH", finding=_scalar("red", direction="lower_better"))
    assert not hasattr(finding, "estimated_gain_pct")
    assert not hasattr(finding, "offensive_relevance")
