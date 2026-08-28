"""RP.0 — the report's data contract, split into 5 explicit sections:

    1. RESULTADO   — `resultado: ReportHeader` (unchanged, already exists)
    2. SETUP       — `setup: SetupAnalysis | None` (SA.6, new here)
    3. EXECUÇÃO    — `execucao: ExecutionSection` (comparisons/performance/dps_gap)
    4. TOP 3 AÇÕES — `top_actions: tuple[Finding, ...]`, EXECUTION-ONLY
    5. CONFIANÇA/AMOSTRA — `confianca: ConfidenceSummary`

This is purely additive: `build_report_contract` ADAPTS an existing
`AnalysisResult` (analysis/pipeline.py) into this shape — nothing in
`analysis/pipeline.py`, `report/text.py`, or `report/html_report.py`
changes, and no existing generation path calls this yet. "Sem quebrar a
geração atual mais do que necessário" (ticket, verbatim) is satisfied
maximally: zero behavior change anywhere, because nothing consumes this
contract yet. RP.1 will render it; RP.2 will populate `setup` for real
(today it's always `None` unless a caller passes one in — no
`analyze_setup()` call exists in the live pipeline yet, deliberately, per
RP.2's own scope).

**Setup and execution findings are structurally separate types** —
`SetupFinding` (analysis/setup_finding.py, SA.1) and `Finding`
(analysis/findings.py) share no base class, no common fields
(`estimated_gain_pct` exists only on `Finding`). `top_actions` is typed
`tuple[Finding, ...]`, so a `SetupFinding` cannot type-check its way in;
`build_report_contract` also asserts this at runtime, defense in depth —
"Top 3: execution-only. Nunca selecionar SetupFinding por
estimated_gain_pct" (ticket, verbatim) is enforced by both the type
system and a runtime check, not just documentation.
"""

from __future__ import annotations

from dataclasses import dataclass

from botgitgud.analysis.comparison import SpellComparison
from botgitgud.analysis.dps_gap import DpsGapReport
from botgitgud.analysis.findings import Finding
from botgitgud.analysis.performance_features import PerformanceFindings
from botgitgud.analysis.pipeline import AnalysisResult
from botgitgud.analysis.setup_analysis import SetupAnalysis
from botgitgud.domain.models import RunManifest
from botgitgud.report.text import ReportHeader


class ReportContractError(ValueError):
    """A `top_actions` entry that isn't an execution `Finding` — never
    silently accepted, never silently dropped.
    """


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
    top_actions: tuple[Finding, ...]
    confianca: ConfidenceSummary
    manifest: RunManifest | None = None


def build_report_contract(
    result: AnalysisResult, *, setup: SetupAnalysis | None = None
) -> ReportContract:
    """Adapts an existing `AnalysisResult` (+ an optional `SetupAnalysis`,
    SA.6) into the 5-section contract. Pure — no I/O, no WCL, no Discord.
    """
    for action in result.top_actions:
        if not isinstance(action, Finding):
            raise ReportContractError(
                f"top_actions must contain only execution Finding objects, got "
                f"{type(action).__name__} — Top 3 is execution-only"
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
    )
