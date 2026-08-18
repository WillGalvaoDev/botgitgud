from __future__ import annotations

from botgitgud.analysis.dps_gap import AbilityGap, DpsGapReport
from botgitgud.analysis.findings import (
    Finding,
    build_findings,
    compute_confidence,
    select_top_actions,
)
from botgitgud.analysis.talent_cluster import BuildDivergence
from botgitgud.domain.spells import SpellInfo


def _empty_dps_gap() -> DpsGapReport:
    return DpsGapReport(
        player_dps=1000.0,
        cohort_median_dps=1200.0,
        gap_pct=-1 / 6,
        duration_s=300.0,
        abilities=(),
        other_pct=0.0,
        n_other=0,
    )


def _ability_gap(**overrides: object) -> AbilityGap:
    defaults: dict[str, object] = {
        "spell": SpellInfo(spell_id=1, name="Chaos Strike", source="wcl"),
        "c_u": 8.0,
        "d_u": 800.0,
        "p_u": 100.0,
        "c_r": 10.0,
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
    }
    defaults.update(overrides)
    return AbilityGap(**defaults)  # type: ignore[arg-type]


def _divergence(**overrides: object) -> BuildDivergence:
    defaults: dict[str, object] = {
        "player_cluster_n": 2,
        "total_n": 34,
        "dominant_cluster_n": 32,
        "dominant_median_dps": 1_240_000.0,
        "player_median_dps": 1_090_000.0,
        "differences": (),
    }
    defaults.update(overrides)
    return BuildDivergence(**defaults)  # type: ignore[arg-type]


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


# -- compute_confidence ----------------------------------------------------------


def test_confidence_alta_needs_all_three_conditions() -> None:
    assert compute_confidence(n=30, any_covariate_relaxed=False, survives_bh=True) == "alta"


def test_confidence_baixa_when_n_below_15() -> None:
    assert compute_confidence(n=14, any_covariate_relaxed=False) == "baixa"


def test_confidence_baixa_when_feature_incomplete_regardless_of_n() -> None:
    assert (
        compute_confidence(n=100, any_covariate_relaxed=False, feature_incomplete=True) == "baixa"
    )


def test_confidence_media_when_covariate_relaxed_even_with_large_n() -> None:
    assert compute_confidence(n=100, any_covariate_relaxed=True) == "média"


def test_confidence_media_when_n_between_15_and_30() -> None:
    assert compute_confidence(n=20, any_covariate_relaxed=False) == "média"


def test_confidence_media_when_bh_not_survived() -> None:
    assert compute_confidence(n=100, any_covariate_relaxed=False, survives_bh=False) == "média"


# -- build_findings: only BUILD and ABILITY_GAP get a real gain (D-31) --------


def test_build_findings_includes_build_divergence_with_real_gain() -> None:
    findings = build_findings(
        build_divergence=_divergence(), dps_gap=_empty_dps_gap(), n=30, relaxed_covariates=()
    )
    build_findings_list = [f for f in findings if f.kind == "BUILD"]
    assert len(build_findings_list) == 1
    assert build_findings_list[0].estimated_gain_pct is not None
    assert build_findings_list[0].estimated_gain_pct > 0


def test_build_findings_skips_ability_gaps_already_ahead_of_cohort() -> None:
    dps_gap = DpsGapReport(
        player_dps=1000.0,
        cohort_median_dps=1200.0,
        gap_pct=-1 / 6,
        duration_s=300.0,
        abilities=(_ability_gap(delta_dps_pct=2.0),),  # ahead of cohort — nothing to gain
        other_pct=0.0,
        n_other=0,
    )
    findings = build_findings(build_divergence=None, dps_gap=dps_gap, n=30, relaxed_covariates=())
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
    )
    findings = build_findings(build_divergence=None, dps_gap=dps_gap, n=30, relaxed_covariates=())
    ability_finding = next(f for f in findings if f.kind == "ABILITY_GAP")
    assert ability_finding.estimated_gain_pct == 6.3


def test_build_findings_ability_gap_low_confidence_propagates() -> None:
    dps_gap = DpsGapReport(
        player_dps=1000.0,
        cohort_median_dps=1200.0,
        gap_pct=-1 / 6,
        duration_s=300.0,
        abilities=(_ability_gap(delta_dps_pct=-6.3, confidence="baixa"),),
        other_pct=0.0,
        n_other=0,
    )
    findings = build_findings(build_divergence=None, dps_gap=dps_gap, n=100, relaxed_covariates=())
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
