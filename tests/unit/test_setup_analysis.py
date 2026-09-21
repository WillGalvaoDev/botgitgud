from __future__ import annotations

import ast
import inspect

from botgitgud.analysis import setup_analysis as setup_analysis_module
from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget
from botgitgud.analysis.benchmark_aggregate import (
    CANONICAL_SECONDARY_STATS,
    BandBenchmark,
    CoverageSummary,
    DescriptiveStats,
    EncounterBenchmark,
    PrevalenceDistribution,
    PrevalenceEntry,
    SetPieceEntry,
    SetSummary,
)
from botgitgud.analysis.setup_analysis import SetupAnalysis, analyze_setup
from botgitgud.analysis.setup_finding import FindingCategory, ObservationCode, sort_setup_findings
from botgitgud.domain.models import GearPiece, SetupProfile, TalentNode
from botgitgud.domain.specs import SpecId

_TARGET = EncounterBenchmarkTarget(SpecId("Warlock", "Demonology"), 1234, 5, 34)
_POLICY = BenchmarkPolicy.default()

_STATS = {
    s: DescriptiveStats(n=30, median=2000.0, p25=1800.0, p75=2200.0)
    for s in CANONICAL_SECONDARY_STATS
}
_EMPTY_STATS = {
    s: DescriptiveStats(n=0, median=None, p25=None, p75=None) for s in CANONICAL_SECONDARY_STATS
}
_EMPTY_SUMMARY_STATS = DescriptiveStats(n=0, median=None, p25=None, p75=None)


def _dist(counts: dict[str, int], n: int) -> PrevalenceDistribution:
    entries = tuple(
        PrevalenceEntry(key=k, n_observed=v, prevalence=(v / n if n else 0.0))
        for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    return PrevalenceDistribution(n_available=n, entries=entries)


def _set_dist(counts: dict[str, int], n: int) -> SetSummary:
    entries = tuple(
        SetPieceEntry(set_id=k, n_players=v, total_pieces=v * 2, prevalence=(v / n if n else 0.0))
        for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    return SetSummary(n_available=n, entries=entries)


def _band(name: str, n: int = 0) -> BandBenchmark:
    talent_counts = {"1:1": 40, "2:2": 10} if n else {}
    trinket_counts = {"100": 40, "200": 10} if n else {}
    set_counts = {"10": 40, "20": 10} if n else {}
    return BandBenchmark(
        band_name=name,
        status="ok" if n >= 8 else "insufficient",
        sample_size=n,
        raw_observation_count=n,
        talent_build_prevalence=_dist(talent_counts, n),
        trinket_prevalence=_dist(trinket_counts, n),
        trinket_pair_prevalence=_dist({}, 0),
        set_summary=_set_dist(set_counts, n),
        secondary_stats=_STATS if name == "p95-99" else _EMPTY_STATS,
        duration_summary=_EMPTY_SUMMARY_STATS,
        item_level_summary=_EMPTY_SUMMARY_STATS,
    )


def _benchmark() -> EncounterBenchmark:
    bands = {"p95-99": _band("p95-99", 50), "p75-95": _band("p75-95"), "p50-75": _band("p50-75")}
    return EncounterBenchmark(
        target=_TARGET,
        policy_version=_POLICY.policy_version,
        total_input_observations=50,
        eligible_observations=50,
        missing_setup_count=0,
        deduped_count=0,
        outside_policy_bands=0,
        coverage=CoverageSummary(50, 50, 0, 1.0),
        bands=bands,
    )


_BM = _benchmark()

_FULL_SETUP = SetupProfile(
    talents=(TalentNode(node_id=1, rank=1, spell_id=1),),
    gear=(GearPiece(slot=12, item_id=100, set_id=10),),
    stats={"Crit": 2000.0, "Haste": 2000.0, "Mastery": 2000.0, "Versatility": 2000.0},
)


def _analyze(setup: SetupProfile | None, benchmark: EncounterBenchmark | None) -> SetupAnalysis:
    return analyze_setup(target=_TARGET, policy=_POLICY, player_setup=setup, benchmark=benchmark)


# -- combines all four categories -----------------------------------------------------


def test_combines_findings_from_all_four_categories() -> None:
    analysis = _analyze(_FULL_SETUP, _BM)
    categories = {f.category for f in analysis.findings}
    assert categories == {
        FindingCategory.TALENT_BUILD,
        FindingCategory.TRINKET,
        FindingCategory.TRINKET_PAIR,
        FindingCategory.SET_BONUS,
        FindingCategory.SECONDARY_STATS,
    }
    # 1 talent + 1 trinket + 1 pair + 1 set + 4 stats = 8
    assert len(analysis.findings) == 8


def test_benchmark_identity_metadata() -> None:
    analysis = _analyze(_FULL_SETUP, _BM)
    assert analysis.benchmark_id == _TARGET.benchmark_id
    assert analysis.player_setup_available is True
    assert analysis.benchmark_available is True


# -- ordering deterministic via sort_setup_findings ------------------------------------


def test_findings_are_pre_sorted_canonically() -> None:
    analysis = _analyze(_FULL_SETUP, _BM)
    assert tuple(analysis.findings) == sort_setup_findings(analysis.findings)


def test_output_deterministic_across_calls() -> None:
    a = _analyze(_FULL_SETUP, _BM)
    b = _analyze(_FULL_SETUP, _BM)
    assert [f.finding_id for f in a.findings] == [f.finding_id for f in b.findings]


# -- missing data degrades honestly, without destroying other categories ----------------


def test_missing_player_setup_degrades_all_categories_but_still_returns_all() -> None:
    analysis = _analyze(None, _BM)
    assert analysis.player_setup_available is False
    assert len(analysis.findings) == 8
    assert all(f.observation is ObservationCode.MISSING_DATA for f in analysis.findings)


def test_missing_benchmark_degrades_all_categories_but_still_returns_all() -> None:
    analysis = _analyze(_FULL_SETUP, None)
    assert analysis.benchmark_available is False
    assert len(analysis.findings) == 8
    assert all(f.observation is ObservationCode.MISSING_DATA for f in analysis.findings)


def test_one_missing_category_does_not_destroy_the_others() -> None:
    """Jogador sem peça de set (mas com talent/trinket/stats presentes) —
    só SET_BONUS degrada; as outras categorias continuam comparadas
    normalmente na MESMA análise.
    """
    setup_no_set = SetupProfile(
        talents=(TalentNode(node_id=1, rank=1, spell_id=1),),
        gear=(GearPiece(slot=12, item_id=100),),  # trinket, sem set_id
        stats={"Crit": 2000.0, "Haste": 2000.0, "Mastery": 2000.0, "Versatility": 2000.0},
    )
    analysis = _analyze(setup_no_set, _BM)
    by_category: dict[FindingCategory, list[ObservationCode]] = {}
    for f in analysis.findings:
        by_category.setdefault(f.category, []).append(f.observation)

    assert by_category[FindingCategory.SET_BONUS] == [ObservationCode.MISSING_DATA]
    assert by_category[FindingCategory.TALENT_BUILD] != [ObservationCode.MISSING_DATA]
    assert by_category[FindingCategory.TRINKET][0] is not ObservationCode.MISSING_DATA
    assert ObservationCode.MISSING_DATA not in by_category[FindingCategory.SECONDARY_STATS]


def test_insufficient_sample_category_does_not_destroy_others() -> None:
    policy = BenchmarkPolicy(min_sample_size=1000)  # torna a evidência disponível insuficiente
    analysis = analyze_setup(target=_TARGET, policy=policy, player_setup=_FULL_SETUP, benchmark=_BM)
    # ainda 8 findings, todos presentes (nenhuma categoria com dado real sumiu)
    assert len(analysis.findings) == 8
    by_category = {f.category: f.observation for f in analysis.findings}
    # categorias com dado real (talent/trinket/set/stats, n=50 ou 30 < 1000) viram
    # INSUFFICIENT_EVIDENCE, nunca MATCHES/DIFFERS/LOW_PREVALENCE fabricados
    for category in (
        FindingCategory.TALENT_BUILD,
        FindingCategory.TRINKET,
        FindingCategory.SET_BONUS,
    ):
        assert by_category[category] is ObservationCode.INSUFFICIENT_EVIDENCE
    # trinket pair nunca teve dado no fixture (n_available=0 em toda banda) —
    # continua CATEGORY_UNAVAILABLE independente da policy, o que é o
    # comportamento correto (zero disponível != evidência insuficiente).
    assert by_category[FindingCategory.TRINKET_PAIR] is ObservationCode.MISSING_DATA


def test_partial_coverage_in_one_category_does_not_affect_others() -> None:
    from botgitgud.analysis.setup_finding import CaveatCode

    bands = {
        "p95-99": BandBenchmark(
            band_name="p95-99",
            status="ok",
            sample_size=100,
            raw_observation_count=100,
            talent_build_prevalence=_dist({"1:1": 40, "2:2": 10}, 72),  # partial: 72 < 100
            trinket_prevalence=_dist({"100": 40, "200": 10}, 100),  # full coverage
            trinket_pair_prevalence=_dist({}, 0),
            set_summary=_set_dist({"10": 40, "20": 10}, 100),
            secondary_stats=_STATS,
            duration_summary=_EMPTY_SUMMARY_STATS,
            item_level_summary=_EMPTY_SUMMARY_STATS,
        ),
        "p75-95": _band("p75-95"),
        "p50-75": _band("p50-75"),
    }
    bm = EncounterBenchmark(
        target=_TARGET,
        policy_version=_POLICY.policy_version,
        total_input_observations=100,
        eligible_observations=100,
        missing_setup_count=0,
        deduped_count=0,
        outside_policy_bands=0,
        coverage=CoverageSummary(100, 100, 0, 1.0),
        bands=bands,
    )
    analysis = _analyze(_FULL_SETUP, bm)
    by_category = {
        f.category: f for f in analysis.findings if f.category == FindingCategory.TALENT_BUILD
    }
    talent = by_category[FindingCategory.TALENT_BUILD]
    trinket = next(f for f in analysis.findings if f.category is FindingCategory.TRINKET)
    assert CaveatCode.PARTIAL_SETUP_COVERAGE in talent.caveats
    assert CaveatCode.PARTIAL_SETUP_COVERAGE not in trinket.caveats


# -- no SetupScore / no global recommendation / no aggregate DPS gain --------------------


def test_no_setup_score_or_global_recommendation() -> None:
    for banned in (
        "SetupScore",
        "setup_score",
        "overall_setup_grade",
        "setup_rating",
        "global_recommendation",
    ):
        assert not hasattr(setup_analysis_module, banned)
    analysis = _analyze(_FULL_SETUP, _BM)
    assert not hasattr(analysis, "score")
    assert not hasattr(analysis, "grade")
    assert not hasattr(analysis, "estimated_gain_pct")


def test_no_estimated_gain_pct_symbol() -> None:
    source = inspect.getsource(setup_analysis_module)
    assert "estimated_gain_pct=" not in source


# -- zero Execution Cohort / WCL / Discord / Store / job -------------------------------------


def test_zero_execution_cohort_wcl_discord_store_job_imports() -> None:
    tree = ast.parse(inspect.getsource(setup_analysis_module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden_prefixes = (
        "botgitgud.analysis.cohort",
        "botgitgud.analysis.cohort_match",
        "botgitgud.analysis.findings",
        "botgitgud.analysis.dps_gap",
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

    source = inspect.getsource(setup_analysis_module)
    assert "open(" not in source
    assert "Path(" not in source
