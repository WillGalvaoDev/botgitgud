"""M29 TEST_PLAN items 1-8: semantic tests for the materiality gate
(RB-1..RB-3) and the Conclusion/positive-observation objects (RB-4/RB-5).

Every `ScalarFinding` below comes from the real `grade_scalar` (never a
hand-rolled grade) — same discipline test_remediation.py already
established for M28.
"""

from __future__ import annotations

from typing import cast

from botgitgud.analysis.dps_gap import AbilityGap, Diagnosis, DpsGapReport
from botgitgud.analysis.findings import ExecutionFinding, Finding, RelevanceFinding
from botgitgud.analysis.materiality import (
    PositiveObservation,
    PositiveObservationBasis,
    build_conclusion,
    collect_material_candidates,
    count_material,
    is_material_ability,
    is_material_execution,
    is_material_uptime,
    select_positive_observation,
)
from botgitgud.analysis.performance_features import (
    PerformanceFindings,
    ScalarFinding,
    UptimeFinding,
    grade_scalar,
)
from botgitgud.analysis.remediation import (
    FindingRemediation,
    Remediation,
    RemediationBasis,
    RemediationKind,
)
from botgitgud.domain.spells import SpellInfo


def _ability_gap(
    *,
    spell_id: int = 123,
    delta_dps_pct: float = -6.0,
    cohort_share: ScalarFinding | None,
) -> AbilityGap:
    return AbilityGap(
        spell=SpellInfo(spell_id, f"Ability {spell_id}", "curated"),
        n_u=5,
        d_u=500,
        p_u=100,
        n_r=10,
        p_r=110,
        d_r=1100,
        delta_d=-600,
        volume=-550,
        efficiency=-100,
        interaction=50,
        delta_dps_pct=delta_dps_pct,
        volume_dps_pct=-5.5,
        efficiency_dps_pct=-1,
        diagnosis=cast(Diagnosis, "usos_perdidos_excedentes"),
        confidence="alta",
        unit_kind="CAST",
        cohort_share=cohort_share,
    )


def _ability_finding(*, estimated_gain_pct: float = 9.0) -> Finding:
    return Finding("ABILITY_GAP", "t", "d", estimated_gain_pct, "alta")


def _dps_gap_report(abilities: tuple[AbilityGap, ...] = ()) -> DpsGapReport:
    return DpsGapReport(1000, 950, 0.0526, 300, abilities, 0, 0)


# -- item 1: bottom-of-distribution share -> red -> material -----------------


def test_ability_share_at_cohort_bottom_is_material() -> None:
    scalar = grade_scalar(1.0, [10.0] * 20, "higher_better")
    assert scalar.grade == "red"

    ability = _ability_gap(delta_dps_pct=-9.0, cohort_share=scalar)
    assert is_material_ability(ability)

    report = _dps_gap_report((ability,))
    count = count_material(
        dps_gap=report,
        findings=[_ability_finding()],
        relevance_findings=[],
        execution_findings=[],
        performance=None,
    )
    assert count == 1


# -- item 2: negative delta_dps_pct but share inside the cohort -> not material


def test_ability_share_inside_cohort_is_not_material_despite_negative_gap() -> None:
    """The test that proves the raw gap stopped being sufficient by
    itself: `delta_dps_pct` is negative (would have entered `Finding`s
    before M29), but the cohort-relative share test grades it green."""
    scalar = grade_scalar(9.5, [float(i) for i in range(20)], "higher_better")
    assert scalar.grade == "green"

    ability = _ability_gap(delta_dps_pct=-9.0, cohort_share=scalar)
    assert not is_material_ability(ability)

    report = _dps_gap_report((ability,))
    count = count_material(
        dps_gap=report,
        findings=[_ability_finding()],
        relevance_findings=[],
        execution_findings=[],
        performance=None,
    )
    assert count == 0


# -- item 3: n < MIN_N_FOR_GRADING -> insufficient -> not material ------------


def test_small_cohort_ability_share_is_insufficient_and_not_material() -> None:
    scalar = grade_scalar(1.0, [10.0] * 5, "higher_better")  # n=5 < MIN_N_FOR_GRADING=15
    assert scalar.grade == "insufficient"

    ability = _ability_gap(delta_dps_pct=-9.0, cohort_share=scalar)
    assert not is_material_ability(ability)


# -- item 4: UPTIME above the cohort -> not material, even at high severity --


def test_uptime_above_cohort_is_not_material_even_with_high_severity() -> None:
    scalar = grade_scalar(95.0, [50.0] * 20, "higher_better")  # far ABOVE the cohort
    assert scalar.grade == "green"  # grade_scalar is already one-tailed/direction-aware
    assert not is_material_uptime(scalar)

    finding = RelevanceFinding(
        kind="UPTIME",
        title="t",
        detail="d",
        offensive_relevance=0.9,
        severity=1.0,  # deliberately maximal — RelevanceFinding's own two-sided severity
        confidence="alta",
        evidence={"quantile": scalar.quantile, "spell_id": 42},
    )
    performance = PerformanceFindings(
        active_time=None,
        deaths=grade_scalar(0.0, [0.0] * 20, "lower_better"),
        downtime=grade_scalar(0.0, [0.0] * 20, "lower_better"),
        uptimes=(UptimeFinding(spell=SpellInfo(42, "X", "curated"), finding=scalar),),
        resource_waste=(),
    )
    count = count_material(
        dps_gap=_dps_gap_report(),
        findings=[],
        relevance_findings=[finding],
        execution_findings=[],
        performance=performance,
    )
    assert count == 0


def test_uptime_below_cohort_with_matching_scalar_is_material() -> None:
    scalar = grade_scalar(5.0, [50.0] * 20, "higher_better")  # far BELOW the cohort
    assert scalar.grade == "red"
    finding = RelevanceFinding(
        kind="UPTIME",
        title="t",
        detail="d",
        offensive_relevance=0.9,
        severity=1.0,
        confidence="alta",
        evidence={"quantile": scalar.quantile, "spell_id": 42},
    )
    performance = PerformanceFindings(
        active_time=None,
        deaths=grade_scalar(0.0, [0.0] * 20, "lower_better"),
        downtime=grade_scalar(0.0, [0.0] * 20, "lower_better"),
        uptimes=(UptimeFinding(spell=SpellInfo(42, "X", "curated"), finding=scalar),),
        resource_waste=(),
    )
    count = count_material(
        dps_gap=_dps_gap_report(),
        findings=[],
        relevance_findings=[finding],
        execution_findings=[],
        performance=performance,
    )
    assert count == 1


def test_uptime_materiality_fails_closed_without_a_matching_scalar() -> None:
    """§7: no comparison base reachable (spell_id absent from the
    performance lookup) -> never material, never an exception."""
    assert not is_material_uptime(None)


# -- item 5: ExecutionFinding red/yellow -> material; green -> not -----------


def test_execution_finding_material_iff_red_or_yellow() -> None:
    red = ExecutionFinding("DEATH", grade_scalar(5.0, [0.0] * 20, "lower_better"))
    green = ExecutionFinding("DEATH", grade_scalar(0.0, [0.0] * 20, "lower_better"))
    assert red.finding.grade == "red"
    assert green.finding.grade == "green"

    assert is_material_execution(red)
    assert not is_material_execution(green)

    count = count_material(
        dps_gap=_dps_gap_report(),
        findings=[],
        relevance_findings=[],
        execution_findings=[red, green],
        performance=None,
    )
    assert count == 1


def test_material_candidates_apply_eligibility_before_materiality() -> None:
    red = grade_scalar(1.0, [10.0] * 20, "higher_better")
    green = grade_scalar(10.0, [1.0] * 20, "higher_better")
    ineligible_finding = _ability_finding(estimated_gain_pct=50.0)
    green_finding = _ability_finding(estimated_gain_pct=10.0)
    death = ExecutionFinding("DEATH", grade_scalar(1.0, [0.0] * 20, "lower_better"))
    direct = Remediation(RemediationKind.DIRECT_ACTION, RemediationBasis.OBSERVED_DEATH)
    no_action = Remediation()
    remediations = (
        FindingRemediation(ineligible_finding, no_action, False),
        FindingRemediation(green_finding, direct, True),
        FindingRemediation(death, direct, True),
    )
    report = _dps_gap_report(
        (
            _ability_gap(spell_id=1, cohort_share=red),
            _ability_gap(spell_id=2, cohort_share=green),
        )
    )

    candidates = collect_material_candidates(
        dps_gap=report, remediations=remediations, performance=None
    )

    assert [candidate.finding for candidate in candidates] == [death]


# -- item 6: nothing material -> empty set, conclusion still produced --------


def test_empty_material_set_still_produces_a_conclusion() -> None:
    count = count_material(
        dps_gap=_dps_gap_report(),
        findings=[],
        relevance_findings=[],
        execution_findings=[],
        performance=None,
    )
    assert count == 0

    conclusion = build_conclusion(
        player_dps=1000.0,
        cohort_dps_values=[900.0] * 20,
        percentile=99.58,
        matched_n=20,
        relaxed_covariates=(),
        material_count=count,
    )
    assert conclusion.material_count == 0
    assert conclusion.standing.grade in {"green", "yellow", "red", "insufficient"}
    assert conclusion.percentile == 99.58


# -- item 7: standing and percentile are distinct, non-contaminating facts ---


def test_standing_and_percentile_are_independent_facts() -> None:
    conclusion = build_conclusion(
        player_dps=1000.0,
        cohort_dps_values=[500.0] * 20,  # player is far ABOVE the paired cohort
        percentile=12.3,  # deliberately a LOW global-ranking percentile
        matched_n=20,
        relaxed_covariates=("has_augmentation",),
        material_count=0,
    )
    assert conclusion.percentile == 12.3
    assert conclusion.standing.grade == "green"
    assert conclusion.standing.user_value == 1000.0
    assert conclusion.sample.matched_n == 20
    assert conclusion.sample.relaxed_covariates == ("has_augmentation",)


# -- item 8: at most one positive observation, deterministic, or none --------


def test_positive_observation_is_deterministic_under_reordering() -> None:
    scalar_a = grade_scalar(18.0, [float(i) for i in range(20)], "higher_better")
    scalar_b = grade_scalar(14.0, [float(i) for i in range(20)], "higher_better")
    assert scalar_a.grade == scalar_b.grade == "green"
    assert scalar_a.quantile is not None and scalar_b.quantile is not None
    assert scalar_a.quantile > scalar_b.quantile

    ability_a = _ability_gap(spell_id=901, delta_dps_pct=1.0, cohort_share=scalar_a)
    ability_b = _ability_gap(spell_id=902, delta_dps_pct=1.0, cohort_share=scalar_b)

    # A `standing` that is deliberately NOT green, so it never competes —
    # isolates this test to the ability candidates' own tie-break.
    standing = grade_scalar(100.0, [900.0] * 20, "higher_better")
    assert standing.grade == "red"

    expected = PositiveObservation(basis=PositiveObservationBasis.ABILITY_ABOVE_COHORT, subject=901)

    order1 = select_positive_observation(
        dps_gap=_dps_gap_report((ability_a, ability_b)), performance=None, standing=standing
    )
    order2 = select_positive_observation(
        dps_gap=_dps_gap_report((ability_b, ability_a)), performance=None, standing=standing
    )
    assert order1 == order2 == expected


def test_positive_observation_absent_without_favourable_evidence() -> None:
    standing = grade_scalar(100.0, [900.0] * 20, "higher_better")
    assert standing.grade == "red"
    assert (
        select_positive_observation(dps_gap=_dps_gap_report(), performance=None, standing=standing)
        is None
    )


def test_positive_observation_picks_overall_standing_when_nothing_else_qualifies() -> None:
    standing = grade_scalar(1000.0, [500.0] * 20, "higher_better")
    assert standing.grade == "green"
    result = select_positive_observation(
        dps_gap=_dps_gap_report(), performance=None, standing=standing
    )
    assert result == PositiveObservation(basis=PositiveObservationBasis.OVERALL_STANDING)


def test_positive_observation_no_death_requires_zero_measured_deaths() -> None:
    """`green` on the deaths scalar means only "not materially worse than
    the cohort" — it is NOT the same fact as "the player did not die". In
    a cohort where dying is common, a player who died once can still
    grade green; NO_DEATH must never be emitted for them (§11/§18.4: the
    basis may not assert a fact the measurement — `user_value` — does not
    establish). Every other candidate is deliberately graded non-green
    here so the only way this test could pass by accident is if NO_DEATH
    itself is suppressed correctly.
    """
    cohort_deaths = [2.0] * 15 + [0.0] * 5  # dying is common in this cohort
    deaths = grade_scalar(1.0, cohort_deaths, "lower_better")  # player DID die
    assert deaths.grade == "green"
    assert deaths.user_value == 1.0  # the measured fact: one death, not zero

    downtime = grade_scalar(100.0, [0.0] * 20, "lower_better")
    assert downtime.grade != "green"

    standing = grade_scalar(100.0, [900.0] * 20, "higher_better")
    assert standing.grade != "green"

    performance = PerformanceFindings(
        active_time=None,
        deaths=deaths,
        downtime=downtime,
        uptimes=(),
        resource_waste=(),
    )
    result = select_positive_observation(
        dps_gap=_dps_gap_report(), performance=performance, standing=standing
    )
    assert result is None


def test_positive_observation_requires_strictly_above_cohort_for_an_ability() -> None:
    """RB-5 names this basis specifically as "above the cohort" — a
    green grade alone (which only means "not in the worst quartile") is
    not enough; the quantile must sit above the median too."""
    # value equal to the reference median -> quantile == 0.5, green, but
    # not "above" the cohort.
    scalar = grade_scalar(9.5, [float(i) for i in range(20)], "higher_better")
    assert scalar.grade == "green"
    assert scalar.quantile == 0.5
    ability = _ability_gap(spell_id=777, delta_dps_pct=0.6, cohort_share=scalar)

    standing = grade_scalar(100.0, [900.0] * 20, "higher_better")
    assert standing.grade == "red"

    result = select_positive_observation(
        dps_gap=_dps_gap_report((ability,)), performance=None, standing=standing
    )
    assert result is None
