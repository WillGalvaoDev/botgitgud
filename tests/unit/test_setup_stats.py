from __future__ import annotations

import ast
import inspect

import pytest

from botgitgud.analysis import setup_stats as setup_stats_module
from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget, PercentileBand
from botgitgud.analysis.benchmark_aggregate import (
    CANONICAL_SECONDARY_STATS,
    BandBenchmark,
    CoverageSummary,
    DescriptiveStats,
    EncounterBenchmark,
    PrevalenceDistribution,
    SetSummary,
)
from botgitgud.analysis.setup_finding import (
    CaveatCode,
    EvidenceLevel,
    ObservationCode,
    Publicability,
    SetupFinding,
)
from botgitgud.analysis.setup_stats import SetupStatsComparisonError, compare_secondary_stats
from botgitgud.domain.models import SetupProfile
from botgitgud.domain.specs import SpecId

_TARGET = EncounterBenchmarkTarget(SpecId("Warlock", "Demonology"), 1234, 5, 34)
_POLICY = BenchmarkPolicy.default()  # p95-99, p75-95, p50-75

_EMPTY_DIST = PrevalenceDistribution(n_available=0, entries=())
_EMPTY_SET = SetSummary(n_available=0, entries=())
_EMPTY_STATS = DescriptiveStats(n=0, median=None, p25=None, p75=None)


def _band(
    name: str, *, stats: dict[str, DescriptiveStats] | None = None, sample_size: int = 0
) -> BandBenchmark:
    filled = {s: _EMPTY_STATS for s in CANONICAL_SECONDARY_STATS}
    if stats:
        filled.update(stats)
    return BandBenchmark(
        band_name=name,
        status="ok" if sample_size >= 8 else "insufficient",
        sample_size=sample_size,
        raw_observation_count=sample_size,
        talent_build_prevalence=_EMPTY_DIST,
        trinket_prevalence=_EMPTY_DIST,
        trinket_pair_prevalence=_EMPTY_DIST,
        set_summary=_EMPTY_SET,
        secondary_stats=filled,
        duration_summary=_EMPTY_STATS,
        item_level_summary=_EMPTY_STATS,
    )


def _empty_bands(policy: BenchmarkPolicy = _POLICY) -> dict[str, BandBenchmark]:
    return {b.name: _band(b.name) for b in policy.bands}


def _benchmark(
    bands: dict[str, BandBenchmark],
    *,
    target: EncounterBenchmarkTarget = _TARGET,
    policy: BenchmarkPolicy = _POLICY,
) -> EncounterBenchmark:
    total = sum(b.sample_size for b in bands.values())
    return EncounterBenchmark(
        target=target,
        policy_version=policy.policy_version,
        total_input_observations=total,
        eligible_observations=total,
        missing_setup_count=0,
        deduped_count=0,
        outside_policy_bands=0,
        coverage=CoverageSummary(total, total, 0, 1.0 if total else 0.0),
        bands=bands,
    )


def _uniform_stats(n: int, median: float, p25: float, p75: float) -> dict[str, DescriptiveStats]:
    stats = DescriptiveStats(n=n, median=median, p25=p25, p75=p75)
    return dict.fromkeys(CANONICAL_SECONDARY_STATS, stats)


def _compare(
    setup: SetupProfile | None,
    benchmark: EncounterBenchmark | None,
    *,
    policy: BenchmarkPolicy = _POLICY,
    target: EncounterBenchmarkTarget = _TARGET,
) -> tuple[SetupFinding, ...]:
    return compare_secondary_stats(
        target=target, policy=policy, player_setup=setup, benchmark=benchmark
    )


def _by_stat(findings: tuple[SetupFinding, ...]) -> dict[str, SetupFinding]:
    result: dict[str, SetupFinding] = {}
    for f in findings:
        assert f.subject.stat_name is not None
        result[f.subject.stat_name] = f
    return result


_TOP_BAND_STATS = _uniform_stats(30, median=2000.0, p25=1800.0, p75=2200.0)
_BM = _benchmark(
    {
        "p95-99": _band("p95-99", stats=_TOP_BAND_STATS, sample_size=30),
        "p75-95": _band("p75-95"),
        "p50-75": _band("p50-75"),
    }
)


def _setup(**values: float) -> SetupProfile:
    return SetupProfile(stats=values)


# -- basic within/outside IQR ------------------------------------------------------


def test_value_within_iqr_matches_common_pattern() -> None:
    setup = _setup(Crit=2000.0, Haste=2000.0, Mastery=2000.0, Versatility=2000.0)
    findings = _compare(setup, _BM)
    assert len(findings) == 4
    for f in findings:
        assert f.observation is ObservationCode.MATCHES_COMMON_PATTERN
        assert f.actionable is False


def test_value_below_p25_differs_from_common_pattern() -> None:
    setup = _setup(Crit=1000.0, Haste=1000.0, Mastery=1000.0, Versatility=1000.0)
    findings = _by_stat(_compare(setup, _BM))
    assert findings["Crit"].observation is ObservationCode.DIFFERS_FROM_COMMON_PATTERN
    assert findings["Crit"].actionable is True


def test_value_above_p75_differs_from_common_pattern() -> None:
    setup = _setup(Crit=3000.0, Haste=3000.0, Mastery=3000.0, Versatility=3000.0)
    findings = _by_stat(_compare(setup, _BM))
    assert findings["Crit"].observation is ObservationCode.DIFFERS_FROM_COMMON_PATTERN


def test_boundary_values_are_within_iqr() -> None:
    setup = _setup(Crit=1800.0, Haste=2200.0, Mastery=2000.0, Versatility=2000.0)
    findings = _by_stat(_compare(setup, _BM))
    assert findings["Crit"].observation is ObservationCode.MATCHES_COMMON_PATTERN
    assert findings["Haste"].observation is ObservationCode.MATCHES_COMMON_PATTERN


# -- raw ratings, never converted --------------------------------------------------


def test_player_value_and_reference_are_raw_never_converted() -> None:
    setup = _setup(Crit=2000.0, Haste=2000.0, Mastery=2000.0, Versatility=2000.0)
    findings = _by_stat(_compare(setup, _BM))
    dist = findings["Crit"].distribution
    assert dist is not None
    assert dist.player_value == 2000.0
    assert dist.benchmark.median == 2000.0
    assert dist.benchmark.p25 == 1800.0
    assert dist.benchmark.p75 == 2200.0
    assert CaveatCode.RAW_RATING_ONLY in findings["Crit"].caveats


def test_never_converts_to_percentage() -> None:
    source = inspect.getsource(setup_stats_module)
    assert "/ 100" not in source
    assert "* 100" not in source
    for banned in ("stat_cap", "cap_value", "def cap", "= cap"):
        assert banned not in source.lower()


def test_no_stat_weight_or_reforge_recommendation_symbols() -> None:
    for banned in ("stat_weight", "reforge", "gem_recommendation", "enchant_recommendation"):
        assert not hasattr(setup_stats_module, banned)


# -- reference band selection: highest percentile with n>0 -------------------------


def test_reference_band_is_the_highest_percentile_with_data() -> None:
    setup = _setup(Crit=2000.0, Haste=2000.0, Mastery=2000.0, Versatility=2000.0)
    findings = _by_stat(_compare(setup, _BM))
    assert findings["Crit"].sample.band_name == "p95-99"


def test_reference_band_falls_back_when_top_band_empty() -> None:
    bm = _benchmark(
        {
            "p95-99": _band("p95-99"),  # sem dado
            "p75-95": _band(
                "p75-95", stats=_uniform_stats(20, 1500.0, 1400.0, 1600.0), sample_size=20
            ),
            "p50-75": _band("p50-75"),
        }
    )
    setup = _setup(Crit=1500.0, Haste=1500.0, Mastery=1500.0, Versatility=1500.0)
    findings = _by_stat(_compare(setup, bm))
    assert findings["Crit"].sample.band_name == "p75-95"


def test_no_cross_band_averaging_or_summing_of_medians() -> None:
    """Duas bandas com dado — a de percentil mais alto (p95-99) é usada
    sozinha como referência, nunca uma média/combinação das duas."""
    bm = _benchmark(
        {
            "p95-99": _band(
                "p95-99", stats=_uniform_stats(10, 3000.0, 2900.0, 3100.0), sample_size=10
            ),
            "p75-95": _band(
                "p75-95", stats=_uniform_stats(20, 1000.0, 900.0, 1100.0), sample_size=20
            ),
            "p50-75": _band("p50-75"),
        }
    )
    setup = _setup(Crit=3000.0, Haste=3000.0, Mastery=3000.0, Versatility=3000.0)
    findings = _by_stat(_compare(setup, bm))
    assert findings["Crit"].distribution is not None
    assert findings["Crit"].distribution.benchmark.median == 3000.0  # nunca (3000+1000)/2=2000
    assert findings["Crit"].sample.band_name == "p95-99"


# -- missing data --------------------------------------------------------------------


def test_missing_player_setup_produces_four_findings() -> None:
    findings = _compare(None, _BM)
    assert len(findings) == len(CANONICAL_SECONDARY_STATS)
    for f in findings:
        assert f.observation is ObservationCode.MISSING_DATA
        assert f.publicability is Publicability.HIDDEN


def test_missing_benchmark_produces_four_findings_with_real_subjects() -> None:
    setup = _setup(Crit=2000.0, Haste=2000.0, Mastery=2000.0, Versatility=2000.0)
    findings = _compare(setup, None)
    assert len(findings) == 4
    stat_names = {f.subject.stat_name for f in findings}
    assert stat_names == set(CANONICAL_SECONDARY_STATS)
    for f in findings:
        assert CaveatCode.BENCHMARK_UNAVAILABLE in f.caveats


def test_player_missing_one_stat_does_not_affect_the_others() -> None:
    setup = _setup(Crit=2000.0)
    findings = _by_stat(_compare(setup, _BM))
    assert findings["Crit"].observation is ObservationCode.MATCHES_COMMON_PATTERN
    assert findings["Mastery"].observation is ObservationCode.MISSING_DATA
    assert CaveatCode.CATEGORY_UNAVAILABLE in findings["Mastery"].caveats


def test_benchmark_with_zero_data_for_a_stat_is_category_unavailable() -> None:
    bm = _benchmark(_empty_bands())
    setup = _setup(Crit=2000.0, Haste=2000.0, Mastery=2000.0, Versatility=2000.0)
    findings = _by_stat(_compare(setup, bm))
    assert findings["Crit"].observation is ObservationCode.MISSING_DATA
    assert CaveatCode.CATEGORY_UNAVAILABLE in findings["Crit"].caveats


def test_mismatched_target_raises() -> None:
    other_target = EncounterBenchmarkTarget(SpecId("Warlock", "Destruction"), 1234, 5, 34)
    setup = _setup(Crit=2000.0)
    with pytest.raises(SetupStatsComparisonError):
        compare_secondary_stats(
            target=other_target, policy=_POLICY, player_setup=setup, benchmark=_BM
        )


# -- evidence / partial coverage --------------------------------------------------------


def test_insufficient_evidence_never_actionable() -> None:
    policy = BenchmarkPolicy(min_sample_size=50)
    bm = _benchmark(
        {
            "p95-99": _band(
                "p95-99", stats=_uniform_stats(4, 2000.0, 1800.0, 2200.0), sample_size=4
            ),
            "p75-95": _band("p75-95"),
            "p50-75": _band("p50-75"),
        },
        policy=policy,
    )
    setup = _setup(Crit=2000.0)
    findings = compare_secondary_stats(
        target=_TARGET, policy=policy, player_setup=setup, benchmark=bm
    )
    crit = _by_stat(findings)["Crit"]
    assert crit.evidence_level in (EvidenceLevel.INSUFFICIENT, EvidenceLevel.WEAK)
    assert crit.observation is ObservationCode.INSUFFICIENT_EVIDENCE
    assert crit.actionable is False


def test_partial_coverage_caveat_when_stat_n_below_band_sample_size() -> None:
    bm = _benchmark(
        {
            "p95-99": _band(
                "p95-99", stats=_uniform_stats(20, 2000.0, 1800.0, 2200.0), sample_size=30
            ),
            "p75-95": _band("p75-95"),
            "p50-75": _band("p50-75"),
        }
    )
    setup = _setup(Crit=2000.0)
    findings = _by_stat(_compare(setup, bm))
    assert CaveatCode.PARTIAL_SETUP_COVERAGE in findings["Crit"].caveats


# -- custom bands / determinism -----------------------------------------------------------


def test_custom_policy_bands() -> None:
    custom_policy = BenchmarkPolicy(
        bands=(PercentileBand("elite", 90.0, 100.0), PercentileBand("rest", 0.0, 90.0)),
        min_sample_size=4,
    )
    bm = _benchmark(
        {
            "elite": _band(
                "elite", stats=_uniform_stats(10, 2000.0, 1900.0, 2100.0), sample_size=10
            ),
            "rest": _band("rest", stats=_uniform_stats(20, 1000.0, 900.0, 1100.0), sample_size=20),
        },
        policy=custom_policy,
    )
    setup = _setup(Crit=2000.0)
    findings = compare_secondary_stats(
        target=_TARGET, policy=custom_policy, player_setup=setup, benchmark=bm
    )
    crit = _by_stat(findings)["Crit"]
    assert crit.sample.band_name == "elite"


def test_finding_id_stable_across_calls() -> None:
    setup = _setup(Crit=2000.0)
    a = _compare(setup, _BM)
    b = _compare(setup, _BM)
    assert [f.finding_id for f in a] == [f.finding_id for f in b]


# -- honesty ----------------------------------------------------------------------------


def test_honesty_no_causal_or_score_language() -> None:
    from botgitgud.analysis.setup_finding import render_observation, validate_setup_language

    setup = _setup(Crit=1000.0, Haste=1000.0, Mastery=1000.0, Versatility=1000.0)
    findings = _by_stat(_compare(setup, _BM))
    assert findings["Crit"].observation is ObservationCode.DIFFERS_FROM_COMMON_PATTERN
    text = render_observation(ObservationCode.DIFFERS_FROM_COMMON_PATTERN)
    validate_setup_language(text)
    for forbidden in ("you need", "reforge", "gem this", "should be at least"):
        assert forbidden not in text.lower()


def test_no_setup_score_or_estimated_gain() -> None:
    source = inspect.getsource(setup_stats_module)
    assert "estimated_gain_pct=" not in source
    for banned in ("SetupScore", "setup_score", "overall_setup_grade", "setup_rating"):
        assert not hasattr(setup_stats_module, banned)


def test_no_normalized_position_field_invented() -> None:
    """`DistributionContext` (SA.1) não carrega posição normalizada — o
    módulo não inventa um campo/unidade nova para isso.
    """
    setup = _setup(Crit=2000.0)
    findings = _by_stat(_compare(setup, _BM))
    dist = findings["Crit"].distribution
    assert dist is not None
    assert not hasattr(dist, "normalized_position")
    assert not hasattr(dist, "score")


# -- zero Execution Cohort / WCL / Discord --------------------------------------------------


def test_zero_execution_cohort_wcl_discord_store_job_imports() -> None:
    tree = ast.parse(inspect.getsource(setup_stats_module))
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

    source = inspect.getsource(setup_stats_module)
    assert "open(" not in source
    assert "Path(" not in source
