"""M28: evidence-bounded actions; phrasing and priority selection belong elsewhere.

ROTATION_RULE_COMPARISON_MISSING: no producer compares guide rules with player
observations. The guide guard is preparatory and never produces remediation.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from botgitgud.analysis.dps_gap import AbilityGap, DpsGapReport
from botgitgud.analysis.findings import ExecutionFinding, Finding, RelevanceFinding
from botgitgud.knowledge.rotation_knowledge import Observability, Origin, RotationRule
from botgitgud.knowledge.spec_slugs import Branch


class RemediationKind(StrEnum):
    DIRECT_ACTION = "DIRECT_ACTION"
    CONDITIONAL_ACTION = "CONDITIONAL_ACTION"
    NO_SPECIFIC_ACTION = "NO_SPECIFIC_ACTION"


class RemediationBasis(StrEnum):
    USE_COUNT = "USE_COUNT"
    AVERAGE_TARGET_DEFICIT = "AVERAGE_TARGET_DEFICIT"
    DAMAGE_PER_USE = "DAMAGE_PER_USE"
    VOLUME_AND_EFFICIENCY = "VOLUME_AND_EFFICIENCY"
    UNPAIRED_BUFFS = "UNPAIRED_BUFFS"
    OBSERVED_DEATH = "OBSERVED_DEATH"
    ACTIVE_PARTICIPATION = "ACTIVE_PARTICIPATION"
    AGGREGATE_RESOURCE_WASTE = "AGGREGATE_RESOURCE_WASTE"
    UPTIME_QUANTILE = "UPTIME_QUANTILE"
    UNMAPPED_EVIDENCE = "UNMAPPED_EVIDENCE"


class RemediationCondition(StrEnum):
    WINDOW_OR_OWN_BUFFS_UNDISTINGUISHED = "WINDOW_OR_OWN_BUFFS_UNDISTINGUISHED"
    MULTIPLE_COMPONENTS_NO_SINGLE_CAUSE = "MULTIPLE_COMPONENTS_NO_SINGLE_CAUSE"
    UPTIME_CAUSE_UNKNOWN = "UPTIME_CAUSE_UNKNOWN"


@dataclass(frozen=True, slots=True)
class Remediation:
    kind: RemediationKind = RemediationKind.NO_SPECIFIC_ACTION
    basis: RemediationBasis = RemediationBasis.UNMAPPED_EVIDENCE
    subject: int | str | None = None  # spell ID or measured resource identifier
    condition: RemediationCondition | None = None
    provenance: RotationRule | None = None

    def __post_init__(self) -> None:
        if (self.kind is RemediationKind.CONDITIONAL_ACTION) != (self.condition is not None):
            raise ValueError("Only conditional actions require an uncertainty condition")


@dataclass(frozen=True, slots=True)
class FindingRemediation:
    finding: Finding | RelevanceFinding | ExecutionFinding
    remediation: Remediation
    coaching_eligible: bool


def rotation_rule_eligible(rule: RotationRule) -> bool:
    """Necessary guide-side guard, NOT evidence of a player violation.

    Named branches fail closed: M28 has no authoritative build-applicability
    proof producer. Even ALL passing this guard lacks a rule/log comparison.
    """
    return (
        rule.origin is Origin.SOURCE_FACT
        and rule.observability is Observability.OBSERVABLE
        and rule.branch is Branch.ALL
    )


def derive_remediation(
    finding: Finding | RelevanceFinding | ExecutionFinding,
    *,
    ability: AbilityGap | None = None,
) -> FindingRemediation:
    """Consume authoritative diagnoses/scalars without inferring their causes."""
    kind = RemediationKind
    basis = RemediationBasis
    condition = RemediationCondition
    result = Remediation()
    if isinstance(finding, ExecutionFinding):
        execution_basis = {
            "DEATH": basis.OBSERVED_DEATH,
            "ACTIVE_TIME": basis.ACTIVE_PARTICIPATION,
            "WASTE": basis.AGGREGATE_RESOURCE_WASTE,
        }.get(finding.category)
        if execution_basis is not None:
            result = Remediation(kind.DIRECT_ACTION, execution_basis, finding.subject)
    elif finding.kind == "ABILITY_GAP" and ability is not None:
        mapping = {
            "usos_perdidos_excedentes": (kind.DIRECT_ACTION, basis.USE_COUNT, None),
            "poucos_alvos": (kind.DIRECT_ACTION, basis.AVERAGE_TARGET_DEFICIT, None),
            "janela_ou_buffs_proprios": (
                kind.CONDITIONAL_ACTION,
                basis.DAMAGE_PER_USE,
                condition.WINDOW_OR_OWN_BUFFS_UNDISTINGUISHED,
            ),
            "volume_e_eficiencia_combinados": (
                kind.CONDITIONAL_ACTION,
                basis.VOLUME_AND_EFFICIENCY,
                condition.MULTIPLE_COMPONENTS_NO_SINGLE_CAUSE,
            ),
            "buffs_nao_pareados": (kind.NO_SPECIFIC_ACTION, basis.UNPAIRED_BUFFS, None),
        }
        mapped = mapping.get(ability.diagnosis)
        if mapped is not None:
            action_kind, action_basis, uncertainty = mapped
            result = Remediation(action_kind, action_basis, ability.spell.spell_id, uncertainty)
    elif finding.kind == "UPTIME" and finding.evidence.get("quantile") is not None:
        spell_id = finding.evidence.get("spell_id")
        if isinstance(spell_id, int):
            result = Remediation(
                kind.CONDITIONAL_ACTION,
                basis.UPTIME_QUANTILE,
                spell_id,
                condition.UPTIME_CAUSE_UNKNOWN,
            )
    return FindingRemediation(finding, result, result.kind is not kind.NO_SPECIFIC_ACTION)


def build_remediations(
    *,
    dps_gap: DpsGapReport,
    findings: Sequence[Finding],
    relevance_findings: Sequence[RelevanceFinding],
    execution_findings: Sequence[ExecutionFinding],
) -> tuple[FindingRemediation, ...]:
    """Associate all candidates, before ranking, preserving analytical objects.

    build_findings emits negative gaps in report order without sorting. Its
    Finding omits identity/diagnosis, so pair with that same authoritative
    sequence; never reconstruct identity from presentation text or scores.
    """
    abilities = (
        [item for item in dps_gap.abilities if item.delta_dps_pct < 0]
        if dps_gap.quantitative_damage_available
        else []
    )
    return (
        tuple(
            derive_remediation(finding, ability=ability)
            for finding, ability in zip(findings, abilities, strict=True)
        )
        + tuple(derive_remediation(item) for item in relevance_findings)
        + tuple(derive_remediation(item) for item in execution_findings)
    )
