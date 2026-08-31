"""RP.3 — o wrapper canônico de renderização: TODO caminho de produção
passa obrigatoriamente pelo `ReportContract` (RP.0) antes de renderizar.

RP.0 definiu o contrato de 5 seções e o guarda de runtime que rejeita um
`SetupFinding` em `top_actions`; RP.1/RP.2 renderizaram a seção SETUP. Mas
`build_report_contract` continuava sem NENHUM call site de produção —
Discord, worker e CLI chamavam `render_report`/`render_html_report`
diretamente com os campos soltos do `AnalysisResult`, então o guarda nunca
executava num relatório real. Este módulo fecha isso.

**Não há bypass.** Os três caminhos reais chamam `render_analysis` (Discord
direto e worker) ou `render_text_report` (CLI); nenhum deles vê os
renderizadores. Os renderizadores continuam com a assinatura que sempre
tiveram — mudá-los para receber `ReportContract` seria um refactor grande
sem ganho, e o ticket permite explicitamente o wrapper canônico — mas o
DADO que chega neles vem sempre do contrato JÁ VALIDADO, nunca do
`AnalysisResult` cru. As funções `*_from_contract` abaixo são as únicas
adaptadoras, e cada uma recebe um `ReportContract`.

**Separação HTML/texto, preservada verbatim.** `render_analysis` devolve
os dois artefatos que a entrega já usava, com os MESMOS papéis: `summary`
é texto plano (cabeçalho + Top 3), é o que vai como mensagem inline do
Discord, e nunca contém HTML; `html` é o anexo, e nunca vira `content` de
mensagem (bot/delivery.py). Este módulo não muda nada disso — só garante
que ambos nascem do contrato.
"""

from __future__ import annotations

from dataclasses import dataclass

from botgitgud.analysis.pipeline import AnalysisResult
from botgitgud.report.contract import ReportContract, build_report_contract
from botgitgud.report.html_report import render_html_report
from botgitgud.report.text import render_header_and_top3, render_report


@dataclass(frozen=True, slots=True)
class RenderedReport:
    """Os dois artefatos de entrega, mais o contrato que os produziu — o
    contrato viaja junto para quem quiser auditar/telemetrar o que foi
    renderizado, nunca para ser renderizado uma segunda vez.
    """

    contract: ReportContract
    summary: str
    html: str


def contract_for(result: AnalysisResult) -> ReportContract:
    """O único ponto onde um `AnalysisResult` vira `ReportContract` no
    caminho de produção. `setup` vem SEMPRE de `result.setup_analysis`
    (RP.2) — nunca de um argumento que um chamador pudesse esquecer de
    passar, que era exatamente como a seção SETUP podia sumir sem ninguém
    notar.
    """
    return build_report_contract(result, setup=result.setup_analysis)


def render_summary_from_contract(contract: ReportContract) -> str:
    """Mensagem inline do Discord: cabeçalho + Top 3, texto plano.
    `top_actions` vem do contrato, ou seja, já passou pelo guarda
    execution-only de RP.0 — a mensagem curta nunca pode listar um
    `SetupFinding`. Não recebe `setup` de propósito: SETUP é seção de
    relatório, não de mensagem.
    """
    return render_header_and_top3(contract.resultado, contract.top_actions)


def render_html_from_contract(contract: ReportContract) -> str:
    return render_html_report(
        contract.resultado,
        contract.execucao.comparisons,
        manifest=contract.manifest,
        performance=contract.execucao.performance,
        dps_gap=contract.execucao.dps_gap,
        top_actions=contract.top_actions,
        duration_s=contract.resultado.duration_max_s,
        setup=contract.setup,
    )


def render_text_from_contract(contract: ReportContract) -> str:
    return render_report(
        contract.resultado,
        contract.execucao.comparisons,
        contract.manifest,
        contract.execucao.performance,
        contract.execucao.dps_gap,
        contract.top_actions,
        setup=contract.setup,
    )


def render_analysis(result: AnalysisResult) -> RenderedReport:
    """O caminho de entrega do Discord (interativo E fila), inteiro: valida
    o contrato uma vez e deriva os dois artefatos dele. Um `SetupFinding`
    em `top_actions` levanta `ReportContractError` AQUI, antes de qualquer
    renderização — nunca chega a virar relatório.
    """
    contract = contract_for(result)
    return RenderedReport(
        contract=contract,
        summary=render_summary_from_contract(contract),
        html=render_html_from_contract(contract),
    )


def render_text_report(result: AnalysisResult) -> str:
    """O caminho do CLI (`analyze`, stdout). Mesmo contrato, mesmo guarda,
    renderizador diferente.
    """
    return render_text_from_contract(contract_for(result))
