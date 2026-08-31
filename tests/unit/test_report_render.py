"""RP.3 — o `ReportContract` (RP.0) virou obrigatório no caminho de
produção.

RP.0 já tem seus próprios testes de FORMA do contrato (test_report_contract
.py). Aqui a pergunta é outra e é a que faltava: o contrato é realmente
CONSTRUÍDO E VALIDADO nos caminhos reais, ou existe um bypass? Cada caminho
é verificado por AST (a chamada existe / os renderizadores não são mais
alcançáveis por fora) e, onde dá para executar offline, também de fato.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import replace

import pytest

import botgitgud.bot.discord_bot as discord_module
import botgitgud.bot.worker as worker_module
import botgitgud.cli as cli_module
import botgitgud.report.render as render_module
from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget
from botgitgud.analysis.findings import Finding
from botgitgud.analysis.pipeline import AnalysisResult
from botgitgud.analysis.setup_analysis import SetupAnalysis
from botgitgud.analysis.setup_finding import (
    BenchmarkSampleRef,
    CaveatCode,
    EvidenceLevel,
    FindingSubject,
    ObservationCode,
    Publicability,
    SetupFinding,
)
from botgitgud.domain.specs import SpecId
from botgitgud.report.contract import ReportContractError, build_report_contract
from botgitgud.report.render import (
    RenderedReport,
    contract_for,
    render_analysis,
    render_html_from_contract,
    render_text_from_contract,
    render_text_report,
)
from botgitgud.report.text import ReportHeader

TARGET = EncounterBenchmarkTarget(
    spec=SpecId("Warlock", "Demonology"), encounter_id=3179, difficulty=5, partition=3
)


# -- helpers -----------------------------------------------------------------------


def _analysis_result() -> AnalysisResult:
    """Um `AnalysisResult` mínimo mas REAL (não um duplo) — o contrato é
    construído a partir do tipo de produção.
    """
    return AnalysisResult(
        header=ReportHeader(
            char_name="Zarad",
            boss_name="Boss",
            class_name="Warlock",
            spec="Demonology",
            reference_n=20,
            duration_min_s=300.0,
            duration_max_s=360.0,
            cohort_warnings=("amostra pequena",),
            matched_covariates=("item_level",),
            relaxed_covariates=("tier_pieces",),
        ),
        comparisons=(),
        manifest=None,  # type: ignore[arg-type]
        reference_pool_members=40,
        matched_cohort_members=20,
    )


def _finding(title: str = "Use o CD mais cedo") -> Finding:
    return Finding(
        kind="CD_TIMING",
        title=title,
        detail="detalhe",
        estimated_gain_pct=3.0,
        confidence="alta",
    )


def _setup_finding(
    *, publicability: Publicability = Publicability.PUBLISHABLE, subject: str = "build-x"
) -> SetupFinding:
    return SetupFinding(
        subject=FindingSubject.talent_build(subject),
        observation=ObservationCode.MATCHES_COMMON_PATTERN,
        evidence_level=EvidenceLevel.STRONG,
        publicability=publicability,
        sample=BenchmarkSampleRef(benchmark_id=TARGET.benchmark_id, band_name=None),
        prevalence=None,
        caveats=(CaveatCode.OBSERVATIONAL_ONLY,),
        actionable=False,
    )


def _setup_analysis(*findings: SetupFinding) -> SetupAnalysis:
    return SetupAnalysis(
        benchmark_id=TARGET.benchmark_id,
        findings=findings or (_setup_finding(),),
        player_setup_available=True,
        benchmark_available=True,
    )


def _called_names(node: ast.AST) -> set[str]:
    return {
        n.func.id
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }


def _imported_names(module: object) -> set[str]:
    tree = ast.parse(inspect.getsource(module))  # type: ignore[arg-type]
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(a.asname or a.name for a in node.names)
    return names


# -- 1: build_report_contract tem call site de produção ----------------------------


def test_build_report_contract_has_a_production_call_site() -> None:
    assert "build_report_contract" in _called_names(ast.parse(inspect.getsource(render_module)))


def test_contract_for_always_carries_the_pipeline_setup_analysis() -> None:
    """O modo de falha que RP.3 fecha: `build_report_contract` tem
    `setup=None` por padrão, então um chamador que esquecesse o argumento
    produziria um relatório sem SETUP sem erro nenhum. `contract_for` não
    aceita esse argumento — SETUP vem sempre do resultado.
    """
    setup = _setup_analysis()
    result = replace(_analysis_result(), setup_analysis=setup)
    assert contract_for(result).setup is setup


# -- 2/3/4: os três caminhos reais passam pelo contrato ----------------------------


def test_discord_direct_path_renders_through_the_contract() -> None:
    tree = ast.parse(inspect.getsource(discord_module))
    handler = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "cmd_analisar"
    )
    assert "render_analysis" in _called_names(handler)
    # e não alcança mais os renderizadores por fora dele
    assert "render_html_report" not in _imported_names(discord_module)
    assert "render_header_and_top3" not in _imported_names(discord_module)


def test_worker_path_renders_through_the_contract() -> None:
    tree = ast.parse(inspect.getsource(worker_module))
    fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_run_analyze"
    )
    assert "render_analysis" in _called_names(fn)
    assert "render_html_report" not in _imported_names(worker_module)
    assert "render_header_and_top3" not in _imported_names(worker_module)


def test_cli_path_renders_through_the_contract() -> None:
    tree = ast.parse(inspect.getsource(cli_module))
    fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_cmd_analyze"
    )
    assert "render_text_report" in _called_names(fn)
    assert "render_report" not in _imported_names(cli_module)


def test_no_production_module_imports_a_renderer_directly() -> None:
    """A prova de "nenhum bypass": fora de `report/`, só `report/render.py`
    importa `render_report`/`render_html_report`/`render_header_and_top3`.
    """
    import pathlib

    root = pathlib.Path(inspect.getfile(discord_module)).parents[1]
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if path.parent.name == "report" or "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            if node.module.startswith("botgitgud.report.render"):
                continue
            for alias in node.names:
                if alias.name in {
                    "render_report",
                    "render_html_report",
                    "render_header_and_top3",
                }:
                    offenders.append(f"{path.name}:{alias.name}")
    assert offenders == [], offenders


# -- 5/6: contrato com e sem setup -------------------------------------------------


def test_setup_present_contract_reaches_both_renderers() -> None:
    setup = _setup_analysis()
    result = replace(_analysis_result(), setup_analysis=setup, top_actions=(_finding(),))
    rendered = render_analysis(result)

    assert isinstance(rendered, RenderedReport)
    assert rendered.contract.setup is setup
    assert "SETUP" in render_text_from_contract(rendered.contract)
    assert "Setup" in rendered.html or "SETUP" in rendered.html


def test_setup_absent_contract_still_produces_a_full_report() -> None:
    result = replace(_analysis_result(), setup_analysis=None, top_actions=(_finding(),))
    rendered = render_analysis(result)

    assert rendered.contract.setup is None
    assert rendered.summary  # RESULTADO + TOP 3 continuam
    assert "<html" in rendered.html


def test_hidden_setup_findings_render_no_setup_section() -> None:
    """Degradação honesta: um finding HIDDEN não vira seção, placeholder,
    nota nem score.
    """
    setup = _setup_analysis(_setup_finding(publicability=Publicability.HIDDEN))
    result = replace(_analysis_result(), setup_analysis=setup, top_actions=(_finding(),))
    text = render_text_from_contract(contract_for(result))
    assert "SETUP" not in text


# -- 7/8/9: o guarda de Top 3 executa em runtime -----------------------------------


def test_setup_finding_in_top_actions_is_rejected_before_rendering() -> None:
    result = replace(_analysis_result(), top_actions=(_setup_finding(),))  # type: ignore[arg-type]
    with pytest.raises(ReportContractError):
        render_analysis(result)


def test_setup_finding_mixed_into_top_actions_is_rejected_by_the_cli_path() -> None:
    result = replace(_analysis_result(), top_actions=(_finding(), _setup_finding()))  # type: ignore[arg-type]
    with pytest.raises(ReportContractError):
        render_text_report(result)


def test_setup_prevalence_never_becomes_estimated_gain_pct() -> None:
    setup = _setup_analysis()
    result = replace(_analysis_result(), setup_analysis=setup, top_actions=(_finding(),))
    contract = contract_for(result)

    assert all(isinstance(a, Finding) for a in contract.top_actions)
    for finding in contract.setup.findings:  # type: ignore[union-attr]
        assert not hasattr(finding, "estimated_gain_pct")
    gains = {a.estimated_gain_pct for a in contract.top_actions}
    assert gains == {3.0}  # exatamente o que a execução produziu, nada de setup


def test_setup_never_changes_the_execution_sections() -> None:
    """Setup não altera grade/execução: as seções EXECUÇÃO, TOP 3 e
    CONFIANÇA saem idênticas com e sem setup.
    """
    base = replace(_analysis_result(), top_actions=(_finding(),))
    without = contract_for(replace(base, setup_analysis=None))
    with_setup = contract_for(replace(base, setup_analysis=_setup_analysis()))

    assert with_setup.execucao == without.execucao
    assert with_setup.top_actions == without.top_actions
    assert with_setup.confianca == without.confianca
    assert with_setup.resultado == without.resultado


# -- 10/11: os renderizadores recebem dado JÁ validado -----------------------------


def test_renderers_consume_contract_fields_only() -> None:
    """Cada adaptador recebe um `ReportContract` e lê apenas dele — nunca
    um `AnalysisResult`.
    """
    for fn in (render_html_from_contract, render_text_from_contract):
        params = list(inspect.signature(fn).parameters.values())
        assert len(params) == 1
        assert params[0].annotation in ("ReportContract", "ReportContract")


def test_html_and_text_are_derived_from_the_same_contract_instance() -> None:
    result = replace(
        _analysis_result(), setup_analysis=_setup_analysis(), top_actions=(_finding(),)
    )
    rendered = render_analysis(result)
    assert rendered.html == render_html_from_contract(rendered.contract)
    assert rendered.summary != rendered.html


# -- 12/13: HTML nunca vira mensagem de Discord ------------------------------------


def test_summary_is_plain_text_and_carries_no_html() -> None:
    result = replace(
        _analysis_result(), setup_analysis=_setup_analysis(), top_actions=(_finding(),)
    )
    summary = render_analysis(result).summary
    for tag in ("<html", "<strong>", "<br", "<div", "<table", "<p>"):
        assert tag not in summary


def test_summary_renderer_cannot_receive_setup_at_all() -> None:
    """A separação estrutural: `render_summary_from_contract` não tem por
    onde receber a seção SETUP — a mensagem inline é cabeçalho + Top 3.
    """
    from botgitgud.report.render import render_summary_from_contract
    from botgitgud.report.text import render_header_and_top3

    assert "setup" not in inspect.signature(render_header_and_top3).parameters
    assert list(inspect.signature(render_summary_from_contract).parameters) == ["contract"]


def test_delivery_never_sends_html_as_message_content() -> None:
    """13: o fallback e a mensagem inline só recebem constantes/texto — o
    `html` só existe como `discord.File`.
    """
    import botgitgud.bot.delivery as delivery_module

    tree = ast.parse(inspect.getsource(delivery_module))
    file_args: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "File"
        ):
            file_args.update(
                a.id for a in ast.walk(node) if isinstance(a, ast.Name) and a.id == "html"
            )
    assert "html" in file_args  # o único destino de `html` é um anexo

    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "content":
            assert not (isinstance(node.value, ast.Name) and node.value.id == "html")


# -- 14: semântica de relatório existente preservada -------------------------------


def test_existing_contract_semantics_are_unchanged() -> None:
    """`build_report_contract` continua exatamente o que era: `contract_for`
    é só a chamada canônica dele, não uma segunda semântica.
    """
    setup = _setup_analysis()
    result = replace(_analysis_result(), setup_analysis=setup, top_actions=(_finding(),))
    assert contract_for(result) == build_report_contract(result, setup=setup)


def test_all_five_sections_are_populated() -> None:
    setup = _setup_analysis()
    result = replace(_analysis_result(), setup_analysis=setup, top_actions=(_finding(),))
    contract = contract_for(result)

    assert contract.resultado.char_name == "Zarad"  # 1. RESULTADO
    assert contract.setup is setup  # 2. SETUP
    assert contract.execucao is not None  # 3. EXECUÇÃO
    assert contract.top_actions  # 4. TOP 3
    assert contract.confianca.reference_pool_members == 40  # 5. CONFIANÇA/AMOSTRA
    assert contract.confianca.relaxed_covariates == ("tier_pieces",)


# -- 15/16: nenhum WCL, nenhum Discord ---------------------------------------------


def test_render_module_touches_no_network_or_discord() -> None:
    imported = _imported_names(render_module)
    tree = ast.parse(inspect.getsource(render_module))
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not any(m.startswith(("botgitgud.wcl", "botgitgud.ingest")) for m in modules), modules
    assert "discord" not in imported
    assert BenchmarkPolicy is not None  # import usado só para o target acima
