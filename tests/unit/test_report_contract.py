from __future__ import annotations

import ast
import inspect

import pytest

from botgitgud.analysis.dps_gap import DpsGapReport
from botgitgud.analysis.findings import Finding, RelevanceFinding, TopPriorities
from botgitgud.analysis.pipeline import AnalysisResult
from botgitgud.analysis.setup_analysis import SetupAnalysis
from botgitgud.analysis.setup_finding import (
    BenchmarkSampleRef,
    EvidenceLevel,
    FindingSubject,
    ObservationCode,
    Publicability,
    SetupFinding,
)
from botgitgud.domain.models import RunManifest
from botgitgud.report import contract as contract_module
from botgitgud.report.contract import (
    ConfidenceSummary,
    ExecutionSection,
    ReportContract,
    ReportContractError,
    build_report_contract,
)
from botgitgud.report.text import ReportHeader

_SAMPLE = BenchmarkSampleRef(benchmark_id="Warlock/Demonology/1/1/1/v1", band_name=None)


def _header(**overrides: object) -> ReportHeader:
    defaults: dict[str, object] = {
        "char_name": "Zarad",
        "boss_name": "Fallen-King Salhadaar",
        "class_name": "Warlock",
        "spec": "Demonology",
        "reference_n": 20,
        "duration_min_s": 300.0,
        "duration_max_s": 360.0,
    }
    defaults.update(overrides)
    return ReportHeader(**defaults)  # type: ignore[arg-type]


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


def _setup_finding() -> SetupFinding:
    return SetupFinding(
        subject=FindingSubject.talent_build("1:1"),
        observation=ObservationCode.MATCHES_COMMON_PATTERN,
        evidence_level=EvidenceLevel.STRONG,
        publicability=Publicability.PUBLISHABLE,
        sample=_SAMPLE,
    )


def _setup_analysis(**overrides: object) -> SetupAnalysis:
    defaults: dict[str, object] = {
        "benchmark_id": "Warlock/Demonology/1/1/1/v1",
        "findings": (_setup_finding(),),
        "player_setup_available": True,
        "benchmark_available": True,
    }
    defaults.update(overrides)
    return SetupAnalysis(**defaults)  # type: ignore[arg-type]


def _result(**overrides: object) -> AnalysisResult:
    defaults: dict[str, object] = {
        "header": _header(),
        "comparisons": (),
        "manifest": None,
    }
    defaults.update(overrides)
    return AnalysisResult(**defaults)  # type: ignore[arg-type]


def _relevance() -> RelevanceFinding:
    return RelevanceFinding("UPTIME", "uptime", "detail", 0.2, 0.9, "alta")


# -- 5-section structure ----------------------------------------------------------


def test_contract_has_five_explicit_sections() -> None:
    contract = build_report_contract(_result())
    assert isinstance(contract, ReportContract)
    assert hasattr(contract, "resultado")
    assert hasattr(contract, "setup")
    assert hasattr(contract, "execucao")
    assert hasattr(contract, "top_actions")
    assert hasattr(contract, "confianca")


def test_resultado_section_is_the_report_header_unchanged() -> None:
    header = _header(player_dps=108297.0, player_percentile=57.0)
    contract = build_report_contract(_result(header=header))
    assert contract.resultado is header


def test_execucao_section_groups_comparisons_performance_dps_gap() -> None:
    dps_gap = DpsGapReport(
        player_dps=1000.0,
        cohort_median_dps=1200.0,
        gap_pct=-1 / 6,
        duration_s=300.0,
        abilities=(),
        other_pct=0.0,
        n_other=0,
    )
    result = _result(dps_gap=dps_gap)
    contract = build_report_contract(result)
    assert isinstance(contract.execucao, ExecutionSection)
    assert contract.execucao.dps_gap is dps_gap
    assert contract.execucao.comparisons == result.comparisons
    assert contract.execucao.performance is result.performance


def test_confianca_section_pulls_from_header_and_result() -> None:
    header = _header(
        cohort_warnings=("amostra pequena",),
        matched_covariates=("item_level",),
        relaxed_covariates=("talent_cluster",),
    )
    result = _result(header=header, reference_pool_members=50, matched_cohort_members=20)
    contract = build_report_contract(result)
    assert isinstance(contract.confianca, ConfidenceSummary)
    assert contract.confianca.reference_pool_members == 50
    assert contract.confianca.matched_cohort_members == 20
    assert contract.confianca.cohort_warnings == ("amostra pequena",)
    assert contract.confianca.matched_covariates == ("item_level",)
    assert contract.confianca.relaxed_covariates == ("talent_cluster",)


def test_manifest_carried_through() -> None:
    from datetime import UTC, datetime

    manifest = RunManifest(
        cohort_id="abc",
        code_version="dead",
        generated_at=datetime(2026, 8, 28, tzinfo=UTC),
        n_members=20,
        wcl_partition=4,
        settings_hash="hash",
    )
    contract = build_report_contract(_result(manifest=manifest))
    assert contract.manifest is manifest


def test_coaching_objects_are_additive_and_carried_through() -> None:
    result = _result(material_priorities=())
    contract = build_report_contract(result)
    assert contract.conclusion is result.conclusion
    assert contract.positive_observation is result.positive_observation
    assert contract.material_priorities == result.material_priorities


# -- setup section: additive, optional, RP.2's job to populate for real -------------


def test_setup_defaults_to_none() -> None:
    contract = build_report_contract(_result())
    assert contract.setup is None


def test_setup_can_be_supplied_explicitly() -> None:
    setup = _setup_analysis()
    contract = build_report_contract(_result(), setup=setup)
    assert contract.setup is setup


def test_pipeline_now_calls_analyze_setup() -> None:
    """RP.0 only built the contract slot; RP.2 is the wiring this test now
    confirms actually happened — `analysis/pipeline.py` calls
    `analyze_setup` for real (checked via AST, not substring).
    """
    import botgitgud.analysis.pipeline as pipeline_module

    tree = ast.parse(inspect.getsource(pipeline_module))
    called = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "analyze_setup"
        for node in ast.walk(tree)
    )
    assert called, "analysis/pipeline.py must call analyze_setup (RP.2)"


# -- top_actions: execution-only, enforced structurally AND at runtime --------------


def test_top_actions_passthrough_when_all_are_findings() -> None:
    findings = (_finding(title="a"), _finding(title="b"))
    priorities = TopPriorities(level1=findings)
    contract = build_report_contract(_result(top_actions=priorities))
    assert contract.top_actions == priorities


def test_top_actions_rejects_a_setup_finding_at_runtime() -> None:
    # Intentionally illegal value: exercises the runtime guard behind the static type.
    priorities = TopPriorities(level1=(_setup_finding(),))  # type: ignore[arg-type]
    bad = _result(top_actions=priorities)
    with pytest.raises(ReportContractError):
        build_report_contract(bad)


def test_top_actions_rejects_setup_finding_mixed_with_real_findings() -> None:
    # Intentionally illegal value: exercises the runtime guard behind the static type.
    priorities = TopPriorities(
        level2=(_relevance(), _setup_finding())  # type: ignore[arg-type]
    )
    mixed = _result(top_actions=priorities)
    with pytest.raises(ReportContractError):
        build_report_contract(mixed)


def test_relevance_finding_is_rejected_from_level1() -> None:
    # Intentionally illegal value: exercises the runtime guard behind the static type.
    priorities = TopPriorities(level1=(_relevance(),))  # type: ignore[arg-type]
    bad = _result(top_actions=priorities)
    with pytest.raises(ReportContractError):
        build_report_contract(bad)


def test_setup_finding_has_no_estimated_gain_pct_structurally() -> None:
    """Setup findings remain structurally outside both priority levels."""
    sf = _setup_finding()
    assert not hasattr(sf, "estimated_gain_pct")


# -- zero WCL / Discord / Store / job -------------------------------------------------


def test_zero_wcl_discord_store_job_imports() -> None:
    tree = ast.parse(inspect.getsource(contract_module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden_prefixes = (
        "discord",
        "duckdb",
        "httpx",
        "aiohttp",
        "botgitgud.ingest.store",
        "botgitgud.wcl",
        "botgitgud.bot",
    )
    for name in imported:
        assert not any(name == p or name.startswith(p + ".") for p in forbidden_prefixes)

    source = inspect.getsource(contract_module)
    assert "open(" not in source
    assert "Path(" not in source
