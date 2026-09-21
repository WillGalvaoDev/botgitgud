"""Typed report contract shared by CLI and Discord.

Setup and execution findings remain separate types. The adapter preserves the
measurement objects, populations, exclusions and availability from analysis;
presentation does not recompute an alternative comparison or a causal gain.
"""

from __future__ import annotations

from dataclasses import dataclass

from botgitgud.analysis.comparison import SpellComparison
from botgitgud.analysis.dps_gap import DpsGapReport
from botgitgud.analysis.findings import Finding, RelevanceFinding, TopPriorities
from botgitgud.analysis.materiality import Conclusion, MaterialCandidate, PositiveObservation
from botgitgud.analysis.performance_features import PerformanceFindings
from botgitgud.analysis.pipeline import AnalysisResult, CoreAbilityReport, ExternalDpsContext
from botgitgud.analysis.proc_analysis import ProcAnalysis
from botgitgud.analysis.setup_analysis import SetupAnalysis
from botgitgud.domain.models import RunManifest
from botgitgud.report.text import ReportHeader


class ReportContractError(ValueError):
    """An entry whose runtime type does not match its priority level."""


@dataclass(frozen=True, slots=True)
class ExecutionSection:
    """3. EXECUÇÃO — como o jogador executou comparado a referências
    compatíveis (Execution Cohort). Mesmos três campos que já existiam em
    `AnalysisResult`, só agrupados sob um nome que corresponde à seção.
    """

    comparisons: tuple[SpellComparison, ...]
    performance: PerformanceFindings | None
    dps_gap: DpsGapReport | None


@dataclass(frozen=True, slots=True)
class ConfidenceSummary:
    """5. CONFIANÇA/AMOSTRA — tudo que diz quão confiável é a comparação de
    EXECUÇÃO (tamanho do pool, covariáveis relaxadas, avisos de amostra
    pequena). Nunca usado para setup — `SetupFinding` já carrega sua
    própria evidência (`evidence_level`/`publicability`, SA.1),
    estruturalmente separada desta.
    """

    reference_pool_members: int | None
    matched_cohort_members: int | None
    cohort_warnings: tuple[str, ...]
    matched_covariates: tuple[str, ...]
    relaxed_covariates: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReportContract:
    resultado: ReportHeader
    setup: SetupAnalysis | None
    execucao: ExecutionSection
    top_actions: TopPriorities
    confianca: ConfidenceSummary
    manifest: RunManifest | None = None
    core_abilities: tuple[CoreAbilityReport, ...] = ()
    proc_analysis: ProcAnalysis | None = None
    external_dps_context: tuple[ExternalDpsContext, ...] = ()
    conclusion: Conclusion | None = None
    positive_observation: PositiveObservation | None = None
    material_priorities: tuple[MaterialCandidate, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.top_actions, tuple):
            object.__setattr__(self, "top_actions", TopPriorities(level1=self.top_actions))


def build_report_contract(
    result: AnalysisResult, *, setup: SetupAnalysis | None = None
) -> ReportContract:
    """Adapts an existing `AnalysisResult` (+ an optional `SetupAnalysis`,
    SA.6) into the 5-section contract. Pure — no I/O, no WCL, no Discord.
    """
    if not isinstance(result.top_actions, TopPriorities):
        raise ReportContractError(
            f"top_actions must be TopPriorities, got {type(result.top_actions).__name__}"
        )
    for action in result.top_actions.level1:
        if not isinstance(action, Finding):
            raise ReportContractError(
                f"top_actions.level1 must contain only Finding objects, got {type(action).__name__}"
            )
    for action in result.top_actions.level2:
        if not isinstance(action, RelevanceFinding):
            raise ReportContractError(
                f"top_actions.level2 must contain only RelevanceFinding objects, got "
                f"{type(action).__name__}"
            )

    return ReportContract(
        resultado=result.header,
        setup=setup,
        execucao=ExecutionSection(
            comparisons=result.comparisons,
            performance=result.performance,
            dps_gap=result.dps_gap,
        ),
        top_actions=result.top_actions,
        confianca=ConfidenceSummary(
            reference_pool_members=result.reference_pool_members,
            matched_cohort_members=result.matched_cohort_members,
            cohort_warnings=result.header.cohort_warnings,
            matched_covariates=result.header.matched_covariates,
            relaxed_covariates=result.header.relaxed_covariates,
        ),
        manifest=result.manifest,
        core_abilities=result.core_abilities,
        proc_analysis=result.proc_analysis,
        external_dps_context=result.external_dps_context,
        conclusion=result.conclusion,
        positive_observation=result.positive_observation,
        material_priorities=result.material_priorities,
    )
