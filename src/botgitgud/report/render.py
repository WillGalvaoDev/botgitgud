"""Canonical construction and plain-text rendering of report contracts."""

from __future__ import annotations

from botgitgud.analysis.pipeline import AnalysisResult
from botgitgud.report.contract import ReportContract, build_report_contract
from botgitgud.report.text import render_report


def contract_for(result: AnalysisResult) -> ReportContract:
    """O único ponto onde um `AnalysisResult` vira `ReportContract` no
    caminho de produção. `setup` vem SEMPRE de `result.setup_analysis`
    (RP.2) — nunca de um argumento que um chamador pudesse esquecer de
    passar, que era exatamente como a seção SETUP podia sumir sem ninguém
    notar.
    """
    return build_report_contract(result, setup=result.setup_analysis)


def render_text_from_contract(contract: ReportContract) -> str:
    return render_report(
        contract.resultado,
        contract.execucao.comparisons,
        contract.manifest,
        contract.execucao.performance,
        contract.execucao.dps_gap,
        contract.top_actions,
        setup=contract.setup,
        confidence=contract.confianca,
        core_abilities=contract.core_abilities,
        proc_analysis=contract.proc_analysis,
        external_dps_context=contract.external_dps_context,
    )


def render_text_report(result: AnalysisResult) -> str:
    """O caminho do CLI (`analyze`, stdout). Mesmo contrato, mesmo guarda,
    renderizador diferente.
    """
    return render_text_from_contract(contract_for(result))
