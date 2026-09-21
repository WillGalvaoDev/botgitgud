from __future__ import annotations

import ast
import inspect

from botgitgud.analysis.benchmark_aggregate import DescriptiveStats
from botgitgud.analysis.setup_analysis import SetupAnalysis
from botgitgud.analysis.setup_finding import (
    BenchmarkSampleRef,
    CaveatCode,
    DistributionContext,
    EvidenceLevel,
    FindingSubject,
    ObservationCode,
    PrevalenceSummary,
    Publicability,
    SetupFinding,
    validate_setup_language,
)
from botgitgud.report import setup_text as setup_text_module
from botgitgud.report.setup_text import render_setup_section

_SAMPLE = BenchmarkSampleRef(benchmark_id="Warlock/Demonology/1/1/1/v1", band_name=None)


def _finding(**overrides: object) -> SetupFinding:
    defaults: dict[str, object] = {
        "subject": FindingSubject.talent_build("1:1"),
        "observation": ObservationCode.MATCHES_COMMON_PATTERN,
        "evidence_level": EvidenceLevel.STRONG,
        "publicability": Publicability.PUBLISHABLE,
        "sample": _SAMPLE,
        "prevalence": PrevalenceSummary(count=40, n_available=50, prevalence=0.8),
    }
    defaults.update(overrides)
    return SetupFinding(**defaults)  # type: ignore[arg-type]


def _analysis(*findings: SetupFinding) -> SetupAnalysis:
    return SetupAnalysis(
        benchmark_id=_SAMPLE.benchmark_id,
        findings=findings,
        player_setup_available=True,
        benchmark_available=True,
    )


# -- basic rendering ---------------------------------------------------------------


def test_none_setup_renders_nothing() -> None:
    assert render_setup_section(None) == []


def test_no_publishable_findings_renders_nothing() -> None:
    hidden = _finding(
        observation=ObservationCode.MISSING_DATA,
        evidence_level=EvidenceLevel.INSUFFICIENT,
        publicability=Publicability.HIDDEN,
        prevalence=None,
    )
    assert render_setup_section(_analysis(hidden)) == []


def test_publishable_finding_renders_the_section_header() -> None:
    lines = render_setup_section(_analysis(_finding()))
    text = "\n".join(lines)
    assert "SETUP" in text


def test_hidden_finding_never_appears_alongside_visible_ones() -> None:
    visible = _finding(subject=FindingSubject.trinket(100))
    hidden = _finding(
        subject=FindingSubject.set_bonus("unresolved"),
        observation=ObservationCode.MISSING_DATA,
        evidence_level=EvidenceLevel.INSUFFICIENT,
        publicability=Publicability.HIDDEN,
        prevalence=None,
    )
    text = "\n".join(render_setup_section(_analysis(visible, hidden)))
    assert "Trinket 100" in text
    assert "unresolved" not in text


def test_caution_finding_is_still_rendered() -> None:
    caution = _finding(
        observation=ObservationCode.INSUFFICIENT_EVIDENCE,
        evidence_level=EvidenceLevel.WEAK,
        publicability=Publicability.CAUTION,
        prevalence=PrevalenceSummary(count=1, n_available=3, prevalence=0.33),
    )
    text = "\n".join(render_setup_section(_analysis(caution)))
    assert "Build de talentos" in text


# -- subject labels -----------------------------------------------------------------


def test_talent_build_label() -> None:
    text = "\n".join(
        render_setup_section(_analysis(_finding(subject=FindingSubject.talent_build("1:1"))))
    )
    assert "Build de talentos" in text


def test_trinket_label_uses_item_id() -> None:
    text = "\n".join(
        render_setup_section(_analysis(_finding(subject=FindingSubject.trinket(12345))))
    )
    assert "Trinket 12345" in text


def test_trinket_pair_label_uses_both_item_ids() -> None:
    f = _finding(subject=FindingSubject.trinket_pair(100, 200))
    text = "\n".join(render_setup_section(_analysis(f)))
    assert "100" in text and "200" in text


def test_set_bonus_label_uses_set_id() -> None:
    f = _finding(subject=FindingSubject.set_bonus("10"))
    text = "\n".join(render_setup_section(_analysis(f)))
    assert "Set 10" in text


def test_secondary_stat_label_uses_stat_name() -> None:
    f = _finding(
        subject=FindingSubject.secondary_stat("Crit"),
        prevalence=None,
        distribution=DistributionContext(
            player_value=2000.0,
            benchmark=DescriptiveStats(n=30, median=2000.0, p25=1800.0, p75=2200.0),
        ),
    )
    text = "\n".join(render_setup_section(_analysis(f)))
    assert "Crit" in text
    assert "2000" in text


# -- prevalence / distribution numbers -----------------------------------------------


def test_prevalence_percentage_and_sample_size_shown() -> None:
    f = _finding(prevalence=PrevalenceSummary(count=10, n_available=50, prevalence=0.2))
    text = "\n".join(render_setup_section(_analysis(f)))
    assert "20%" in text
    assert "n=50" in text


def test_distribution_shows_interquartile_range() -> None:
    f = _finding(
        subject=FindingSubject.secondary_stat("Haste"),
        prevalence=None,
        distribution=DistributionContext(
            player_value=1500.0,
            benchmark=DescriptiveStats(n=20, median=1600.0, p25=1400.0, p75=1800.0),
        ),
    )
    text = "\n".join(render_setup_section(_analysis(f)))
    assert "1400" in text and "1800" in text


# -- caveats / disclaimers -----------------------------------------------------------


def test_partial_coverage_caveat_shown_per_finding() -> None:
    f = _finding(caveats=(CaveatCode.PARTIAL_SETUP_COVERAGE,))
    text = "\n".join(render_setup_section(_analysis(f)))
    assert "cobertura parcial" in text


def test_talent_names_unresolved_disclaimer_shown_once() -> None:
    f1 = _finding(
        subject=FindingSubject.talent_build("1:1"), caveats=(CaveatCode.TALENT_NAMES_UNRESOLVED,)
    )
    text = "\n".join(render_setup_section(_analysis(f1)))
    assert text.count("Nomes de talentos") == 1


def test_raw_rating_only_disclaimer_shown_once() -> None:
    f1 = _finding(
        subject=FindingSubject.secondary_stat("Crit"),
        prevalence=None,
        distribution=DistributionContext(
            player_value=2000.0,
            benchmark=DescriptiveStats(n=30, median=2000.0, p25=1800.0, p75=2200.0),
        ),
        caveats=(CaveatCode.RAW_RATING_ONLY,),
    )
    text = "\n".join(render_setup_section(_analysis(f1)))
    assert "rating bruto" in text


def test_no_disclaimers_when_no_relevant_caveats() -> None:
    f = _finding(caveats=())
    text = "\n".join(render_setup_section(_analysis(f)))
    assert "Nomes de talentos" not in text
    assert "rating bruto" not in text


# -- observational language, never causal --------------------------------------------


def test_every_rendered_line_passes_the_language_guard() -> None:
    findings = (
        _finding(observation=ObservationCode.MATCHES_COMMON_PATTERN),
        _finding(
            subject=FindingSubject.trinket(1),
            observation=ObservationCode.DIFFERS_FROM_COMMON_PATTERN,
        ),
        _finding(
            subject=FindingSubject.trinket(2),
            observation=ObservationCode.LOW_PREVALENCE,
            prevalence=PrevalenceSummary(0, 50, 0.0),
        ),
        _finding(
            subject=FindingSubject.trinket(3),
            observation=ObservationCode.INSUFFICIENT_EVIDENCE,
            evidence_level=EvidenceLevel.WEAK,
            publicability=Publicability.CAUTION,
            prevalence=PrevalenceSummary(1, 3, 0.33),
        ),
    )
    lines = render_setup_section(_analysis(*findings))
    for line in lines:
        if line and line != "=" * 42:
            validate_setup_language(line)


def test_zero_prevalence_never_becomes_bad_build_language() -> None:
    f = _finding(
        subject=FindingSubject.trinket(999),
        observation=ObservationCode.LOW_PREVALENCE,
        prevalence=PrevalenceSummary(count=0, n_available=50, prevalence=0.0),
    )
    text = "\n".join(render_setup_section(_analysis(f)))
    for forbidden in ("bad", "worse", "wrong", "upgrade", "should", "optimal", "best"):
        assert forbidden not in text.lower()


# -- historical Discord-HTML bug: never emits raw HTML tags --------------------------


def test_render_setup_section_never_contains_html_tags() -> None:
    findings = (
        _finding(),
        _finding(subject=FindingSubject.trinket(1)),
        _finding(subject=FindingSubject.trinket_pair(1, 2)),
        _finding(subject=FindingSubject.set_bonus("10")),
        _finding(
            subject=FindingSubject.secondary_stat("Crit"),
            prevalence=None,
            distribution=DistributionContext(
                player_value=2000.0,
                benchmark=DescriptiveStats(n=30, median=2000.0, p25=1800.0, p75=2200.0),
            ),
        ),
    )
    lines = render_setup_section(_analysis(*findings))
    for line in lines:
        assert "<" not in line
        assert ">" not in line


# -- renderer ownership -----------------------------------------------------------


def test_render_report_is_wired_to_the_setup_section() -> None:
    """RP.2: `render_report` (report/text.py) now actually calls this
        module — the integration this docstring's RP.1 version explicitly
    deferred. Other formats use their own rendering
        (RP.1's own design principle: no shared tag-generation code), so it
        imports `setup_text`'s pure data helpers, never `render_setup_section`
        itself.
    """
    import botgitgud.report.text as text_module

    tree = ast.parse(inspect.getsource(text_module))
    found = any(
        isinstance(node, ast.ImportFrom) and node.module == "botgitgud.report.setup_text"
        for node in ast.walk(tree)
    )
    assert found, "render_report must import setup_text (RP.2)"


# -- zero WCL / Discord / Store / job -------------------------------------------------


def test_zero_wcl_discord_store_job_imports() -> None:
    tree = ast.parse(inspect.getsource(setup_text_module))
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
