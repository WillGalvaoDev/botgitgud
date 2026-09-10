"""M31 semantic ordering tests: precedence without cross-kind scores."""

from __future__ import annotations

import itertools
from typing import Literal

from botgitgud.analysis.findings import (
    ExecutionCategory,
    ExecutionFinding,
    Finding,
    RelevanceFinding,
)
from botgitgud.analysis.grading import Grade
from botgitgud.analysis.materiality import MaterialCandidate
from botgitgud.analysis.performance_features import grade_scalar
from botgitgud.analysis.prioritization import select_material_priorities
from botgitgud.analysis.remediation import (
    Remediation,
    RemediationBasis,
    RemediationCondition,
    RemediationKind,
)


def _ability(name: str, grade: Grade, gain: float) -> MaterialCandidate:
    finding = Finding("ABILITY_GAP", name, "detail", gain, "alta")
    remediation = Remediation(RemediationKind.DIRECT_ACTION, RemediationBasis.USE_COUNT, name)
    return MaterialCandidate(finding, remediation, grade)


def _execution(
    kind: ExecutionCategory, grade: Literal["red", "yellow"], value: float = 1.0
) -> MaterialCandidate:
    reference = [0.0] * 20 if grade == "red" else [0.0] * 15 + [1.0] * 5
    scalar = grade_scalar(value, reference, "lower_better")
    assert scalar.grade == grade
    finding = ExecutionFinding(kind, scalar)
    basis = (
        RemediationBasis.OBSERVED_DEATH
        if kind == "DEATH"
        else RemediationBasis.ACTIVE_PARTICIPATION
    )
    return MaterialCandidate(
        finding, Remediation(RemediationKind.DIRECT_ACTION, basis), scalar.grade
    )


def _uptime(name: str, grade: Grade) -> MaterialCandidate:
    finding = RelevanceFinding("UPTIME", name, "detail", 0.5, 1.0, "alta", {"spell_id": 42})
    remediation = Remediation(
        RemediationKind.CONDITIONAL_ACTION,
        RemediationBasis.UPTIME_QUANTILE,
        42,
        RemediationCondition.UPTIME_CAUSE_UNKNOWN,
    )
    return MaterialCandidate(finding, remediation, grade)


def test_red_band_precedes_semantic_layer() -> None:
    death = _execution("DEATH", "yellow")
    gap = _ability("gap", "red", 1.0)
    assert select_material_priorities([death, gap]) == (gap, death)


def test_participation_loss_precedes_output_deficit_within_band() -> None:
    death = _execution("DEATH", "red")
    gap = _ability("gap", "red", 999.0)
    assert select_material_priorities([gap, death]) == (death, gap)


def test_death_red_precedes_ability_gap_yellow() -> None:
    death = _execution("DEATH", "red")
    gap = _ability("gap", "yellow", 999.0)
    assert select_material_priorities([gap, death]) == (death, gap)


def test_ability_gaps_use_gain_only_within_the_same_kind() -> None:
    smaller = _ability("smaller", "red", 2.0)
    larger = _ability("larger", "red", 20.0)
    assert select_material_priorities([smaller, larger]) == (larger, smaller)


def test_ability_gain_never_changes_order_against_another_kind() -> None:
    uptime = _uptime("uptime", "red")
    small = select_material_priorities([uptime, _ability("gap", "red", 0.01)])
    huge = select_material_priorities([uptime, _ability("gap", "red", 9999.0)])
    assert [type(item.finding) for item in small] == [type(item.finding) for item in huge]
    assert all(item.ordering_is_arbitrary for item in small)
    assert all(item.ordering_is_arbitrary for item in huge)


def test_empty_and_cut_to_three() -> None:
    assert select_material_priorities([]) == ()
    candidates = [_ability(str(index), "red", float(index)) for index in range(5)]
    assert select_material_priorities(candidates) == tuple(reversed(candidates[2:]))


def test_order_is_deterministic_under_every_permutation() -> None:
    candidates = (
        _execution("ACTIVE_TIME", "red", 1.0),
        _execution("DEATH", "red", 1.0),
        _ability("b", "red", 5.0),
        _ability("a", "red", 5.0),
    )
    expected = select_material_priorities(candidates)
    for permutation in itertools.permutations(candidates):
        assert select_material_priorities(permutation) == expected
    assert expected[0].ordering_is_arbitrary
    assert expected[1].ordering_is_arbitrary


def test_drahzhul_regression_death_first_and_green_abilities_absent() -> None:
    death = _execution("DEATH", "red")
    active_time = _execution("ACTIVE_TIME", "yellow")
    material_gaps = [
        _ability("The Last Light", "red", 23.72),
        _ability("Soul Barrage", "red", 2.43),
        _ability("Blaze", "red", 2.33),
        _ability("Blighted Maw", "red", 2.26),
    ]
    # The two historical false positives are deliberately absent: M29's
    # material set excludes their green grades before M31 receives input.
    result = select_material_priorities([active_time, *material_gaps, death])
    assert result[0] == death
    selected_findings = [candidate.finding for candidate in result[1:]]
    assert all(isinstance(finding, Finding) for finding in selected_findings)
    assert [finding.title for finding in selected_findings if isinstance(finding, Finding)] == [
        "The Last Light",
        "Soul Barrage",
    ]
