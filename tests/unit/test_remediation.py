from dataclasses import fields, replace
from typing import cast

import pytest

from botgitgud.analysis.dps_gap import AbilityGap, Diagnosis, DpsGapReport
from botgitgud.analysis.findings import ExecutionCategory, ExecutionFinding, Finding, build_findings
from botgitgud.analysis.performance_features import grade_scalar
from botgitgud.analysis.remediation import (
    Remediation,
    build_remediations,
    derive_remediation,
    rotation_rule_eligible,
)
from botgitgud.analysis.remediation import (
    RemediationBasis as Basis,
)
from botgitgud.analysis.remediation import (
    RemediationCondition as Condition,
)
from botgitgud.analysis.remediation import (
    RemediationKind as Kind,
)
from botgitgud.domain.spells import SpellInfo
from botgitgud.knowledge.rotation_knowledge import (
    Condition as RuleCondition,
)
from botgitgud.knowledge.rotation_knowledge import (
    Observability,
    Origin,
    RotationRule,
    Section,
)
from botgitgud.knowledge.spec_slugs import Branch


def _ability(diagnosis: str) -> AbilityGap:
    return AbilityGap(
        spell=SpellInfo(123, "Measured ability", "curated"),
        delta_dps_pct=-6,
        volume_dps_pct=-5.5,
        efficiency_dps_pct=-1,
        diagnosis=cast(Diagnosis, diagnosis),
        confidence="baixa",
        unit_kind="DAMAGE_EVENT",
        review_eligible=True,
    )


@pytest.mark.parametrize(
    ("diagnosis", "kind", "basis", "condition"),
    [
        ("usos_perdidos_excedentes", Kind.NO_SPECIFIC_ACTION, Basis.UNMAPPED_EVIDENCE, None),
        ("poucos_alvos", Kind.NO_SPECIFIC_ACTION, Basis.UNMAPPED_EVIDENCE, None),
        ("janela_ou_buffs_proprios", Kind.NO_SPECIFIC_ACTION, Basis.UNMAPPED_EVIDENCE, None),
        ("volume_e_eficiencia_combinados", Kind.NO_SPECIFIC_ACTION, Basis.UNMAPPED_EVIDENCE, None),
        ("buffs_nao_pareados", Kind.NO_SPECIFIC_ACTION, Basis.UNPAIRED_BUFFS, None),
    ],
)
def test_diagnoses_preserve_evidence_limits(
    diagnosis: str,
    kind: Kind,
    basis: Basis,
    condition: Condition | None,
) -> None:
    ability = _ability(diagnosis)
    report = DpsGapReport(100, 106, -6, 100, (ability,), 0, 0)
    findings, relevance, execution = build_findings(
        dps_gap=report,
        n=60,
        relaxed_covariates=(),
        performance=None,
        player_damage_share={},
    )
    (associated,) = build_remediations(
        dps_gap=report,
        findings=findings,
        relevance_findings=relevance,
        execution_findings=execution,
    )
    assert associated.finding is findings[0]
    assert isinstance(associated.finding, Finding)
    assert associated.finding.confidence == "baixa"
    subject = None if basis is Basis.UNMAPPED_EVIDENCE else 123
    assert associated.remediation == Remediation(kind, basis, subject, condition)
    assert associated.coaching_eligible is (kind is not Kind.NO_SPECIFIC_ACTION)
    assert associated.remediation.provenance is None


def test_observed_output_deficit_is_only_coachable_when_review_eligible() -> None:
    ability = replace(_ability("observed_output_deficit"), review_eligible=True)
    finding = Finding("ABILITY_GAP", "Measured ability", "", "baixa")
    result = derive_remediation(finding, ability=ability)
    assert result.remediation == Remediation(
        Kind.CONDITIONAL_ACTION,
        Basis.OBSERVED_OUTPUT_DEFICIT,
        123,
        Condition.CAUSE_NOT_IDENTIFIED,
    )
    assert result.coaching_eligible


def test_unknown_diagnosis_fails_closed_despite_title_and_gain() -> None:
    finding = Finding("ABILITY_GAP", "usos_perdidos_excedentes", "use on cooldown", "alta")
    result = derive_remediation(finding, ability=_ability("future_diagnosis"))
    assert result.finding is finding
    assert result.remediation == Remediation()
    assert not result.coaching_eligible
    assert derive_remediation(finding).remediation == Remediation()


@pytest.mark.parametrize(
    ("category", "basis", "subject"),
    [
        ("DEATH", Basis.OBSERVED_DEATH, None),
        ("ACTIVE_TIME", Basis.ACTIVE_PARTICIPATION, None),
        ("WASTE", Basis.AGGREGATE_RESOURCE_WASTE, "mana"),
    ],
)
def test_execution_prescribes_only_measured_abstraction(
    category: ExecutionCategory,
    basis: Basis,
    subject: str | None,
) -> None:
    scalar = grade_scalar(10, [0] * 20, "lower_better")
    finding = ExecutionFinding(category, scalar, subject)
    result = derive_remediation(finding)
    assert result.finding is finding
    assert result.remediation == Remediation(Kind.DIRECT_ACTION, basis, subject)
    assert result.coaching_eligible


def test_active_time_and_downtime_keep_one_abstraction() -> None:
    active = ExecutionFinding("ACTIVE_TIME", grade_scalar(20, [100] * 20, "higher_better"))
    downtime = ExecutionFinding("ACTIVE_TIME", grade_scalar(80, [0] * 20, "lower_better"))
    assert derive_remediation(active).remediation == derive_remediation(downtime).remediation


def test_uptime_quantile_does_not_establish_cause() -> None:
    finding = Finding(
        "UPTIME", "arbitrary", "arbitrary", "baixa", {"quantile": 0.01, "spell_id": 123}
    )
    assert not derive_remediation(finding).coaching_eligible
    result = derive_remediation(finding, entity_eligible=True)
    assert result.remediation == Remediation(
        Kind.CONDITIONAL_ACTION,
        Basis.UPTIME_QUANTILE,
        123,
        Condition.UPTIME_CAUSE_UNKNOWN,
    )
    assert result.coaching_eligible
    assert not derive_remediation(replace(finding, evidence={})).coaching_eligible


def test_absence_requires_no_action_payload_and_schema_has_no_prose() -> None:
    assert Remediation().subject is None
    assert {field.name for field in fields(Remediation)} == {
        "kind",
        "basis",
        "subject",
        "condition",
        "provenance",
    }
    with pytest.raises(ValueError):
        Remediation(Kind.CONDITIONAL_ACTION)


@pytest.mark.parametrize("branch", list(Branch))
@pytest.mark.parametrize("origin", list(Origin))
@pytest.mark.parametrize("observable", [True, False])
def test_guide_guard_requires_source_observability_and_applicability(
    branch: Branch,
    origin: Origin,
    observable: bool,
) -> None:
    rule = RotationRule(
        section=Section.OPENER if observable else Section.SINGLE_TARGET,
        branch=branch,
        ordinal=1,
        action_spell_ids=(123,),
        alternative=False,
        condition=RuleCondition(),
        origin=origin,
        observability=Observability.OBSERVABLE if observable else Observability.NOT_OBSERVABLE,
    )
    assert rotation_rule_eligible(rule) is (
        observable and origin is Origin.SOURCE_FACT and branch is Branch.ALL
    )
    # A passing guard still supplies no comparison and cannot become provenance.
    finding = Finding("ABILITY_GAP", "Measured ability", "", "alta")
    assert (
        derive_remediation(finding, ability=_ability("poucos_alvos")).remediation.provenance is None
    )


def test_association_preserves_report_order_and_omits_non_findings() -> None:
    first = _ability("buffs_nao_pareados")
    second = replace(_ability("poucos_alvos"), spell=SpellInfo(456, "Other", "curated"))
    report = DpsGapReport(100, 106, -6, 100, (replace(first, delta_dps_pct=1), first, second), 0, 0)
    findings, relevance, execution = build_findings(
        dps_gap=report,
        n=60,
        relaxed_covariates=(),
        performance=None,
        player_damage_share={},
    )
    results = build_remediations(
        dps_gap=report,
        findings=findings,
        relevance_findings=relevance,
        execution_findings=execution,
    )
    assert [item.remediation.subject for item in results] == [123, None]
    assert [item.coaching_eligible for item in results] == [False, False]
    assert all(item.finding is finding for item, finding in zip(results, findings, strict=True))
    assert (
        build_remediations(
            dps_gap=replace(report, quantitative_damage_available=False),
            findings=[],
            relevance_findings=[],
            execution_findings=[],
        )
        == ()
    )
