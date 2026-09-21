from __future__ import annotations

import ast
import inspect

import pytest

from botgitgud.analysis import setup_setbonus as setup_setbonus_module
from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget, PercentileBand
from botgitgud.analysis.benchmark_aggregate import (
    CANONICAL_SECONDARY_STATS,
    BandBenchmark,
    CoverageSummary,
    DescriptiveStats,
    EncounterBenchmark,
    PrevalenceDistribution,
    SetPieceEntry,
    SetSummary,
)
from botgitgud.analysis.setup_finding import (
    CaveatCode,
    EvidenceLevel,
    ObservationCode,
    Publicability,
    SetupFinding,
)
from botgitgud.analysis.setup_setbonus import SetupSetBonusComparisonError, compare_set_bonus
from botgitgud.domain.models import GearPiece, SetupProfile
from botgitgud.domain.specs import SpecId

_TARGET = EncounterBenchmarkTarget(SpecId("Warlock", "Demonology"), 1234, 5, 34)
_POLICY = BenchmarkPolicy.default()

_EMPTY_STATS = DescriptiveStats(n=0, median=None, p25=None, p75=None)
_EMPTY_DIST = PrevalenceDistribution(n_available=0, entries=())


def _set_summary(counts: dict[str, int], n_available: int) -> SetSummary:
    entries = tuple(
        SetPieceEntry(
            set_id=k,
            n_players=v,
            total_pieces=v * 2,
            prevalence=(v / n_available if n_available else 0.0),
        )
        for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    return SetSummary(n_available=n_available, entries=entries)


def _band(
    name: str,
    *,
    counts: dict[str, int] | None = None,
    n_available: int = 0,
    sample_size: int | None = None,
) -> BandBenchmark:
    ss = sample_size if sample_size is not None else n_available
    return BandBenchmark(
        band_name=name,
        status="ok" if ss >= 8 else "insufficient",
        sample_size=ss,
        raw_observation_count=ss,
        talent_build_prevalence=_EMPTY_DIST,
        trinket_prevalence=_EMPTY_DIST,
        trinket_pair_prevalence=_EMPTY_DIST,
        set_summary=_set_summary(counts or {}, n_available),
        secondary_stats={s: _EMPTY_STATS for s in CANONICAL_SECONDARY_STATS},
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


def _single_band_benchmark(
    counts: dict[str, int], n_available: int, *, sample_size: int | None = None
) -> EncounterBenchmark:
    name = _POLICY.bands[0].name
    bands = _empty_bands()
    bands[name] = _band(name, counts=counts, n_available=n_available, sample_size=sample_size)
    return _benchmark(bands)


def _setup(*set_ids: int) -> SetupProfile:
    return SetupProfile(
        gear=tuple(GearPiece(slot=i, item_id=100 + i, set_id=sid) for i, sid in enumerate(set_ids))
    )


def _compare(
    setup: SetupProfile | None,
    benchmark: EncounterBenchmark | None,
    *,
    policy: BenchmarkPolicy = _POLICY,
    target: EncounterBenchmarkTarget = _TARGET,
) -> tuple[SetupFinding, ...]:
    return compare_set_bonus(target=target, policy=policy, player_setup=setup, benchmark=benchmark)


_BM = _single_band_benchmark({"10": 40, "20": 10}, 50)


# -- common / uncommon / unobserved ---------------------------------------------------


def test_common_set_matches_pattern() -> None:
    findings = _compare(_setup(10), _BM)
    assert len(findings) == 1
    assert findings[0].observation is ObservationCode.MATCHES_COMMON_PATTERN
    assert findings[0].actionable is False


def test_uncommon_set_differs_from_pattern() -> None:
    findings = _compare(_setup(20), _BM)
    assert findings[0].observation is ObservationCode.DIFFERS_FROM_COMMON_PATTERN
    assert findings[0].actionable is True


def test_unobserved_set_is_low_prevalence() -> None:
    findings = _compare(_setup(999), _BM)
    assert findings[0].observation is ObservationCode.LOW_PREVALENCE
    assert findings[0].prevalence is not None and findings[0].prevalence.count == 0


# -- denominators / aggregation --------------------------------------------------------


def test_denominator_is_set_summary_n_available_not_band_sample_size() -> None:
    bm = _single_band_benchmark({"10": 36}, 72, sample_size=100)
    findings = _compare(_setup(10), bm)
    assert findings[0].prevalence is not None
    assert findings[0].prevalence.n_available == 72
    assert findings[0].prevalence.count == 36


def test_aggregation_sums_across_bands_not_averages() -> None:
    bands = {
        "p95-99": _band("p95-99", counts={"10": 2, "20": 8}, n_available=10),
        "p75-95": _band("p75-95", counts={"10": 30, "20": 10}, n_available=40),
        "p50-75": _band("p50-75", counts={}, n_available=0),
    }
    bm = _benchmark(bands)
    findings = _compare(_setup(20), bm)
    assert findings[0].prevalence is not None
    assert findings[0].prevalence.count == 18
    assert findings[0].prevalence.n_available == 50
    assert findings[0].prevalence.prevalence == pytest.approx(0.36)


# -- multiple distinct sets equipped -----------------------------------------------------


def test_two_distinct_sets_produce_two_independent_findings() -> None:
    findings = _compare(_setup(10, 20), _BM)
    by_set = {f.subject.set_id: f.observation for f in findings}
    assert by_set == {
        "10": ObservationCode.MATCHES_COMMON_PATTERN,
        "20": ObservationCode.DIFFERS_FROM_COMMON_PATTERN,
    }


# -- missing data -----------------------------------------------------------------------


def test_missing_player_setup() -> None:
    findings = _compare(None, _BM)
    assert len(findings) == 1
    assert findings[0].observation is ObservationCode.MISSING_DATA
    assert findings[0].publicability is Publicability.HIDDEN


def test_no_set_pieces_equipped() -> None:
    findings = _compare(_setup(), _BM)
    assert findings[0].observation is ObservationCode.MISSING_DATA
    assert CaveatCode.CATEGORY_UNAVAILABLE in findings[0].caveats


def test_missing_benchmark_uses_real_set_ids() -> None:
    findings = _compare(_setup(10), None)
    assert findings[0].subject.set_id == "10"
    assert CaveatCode.BENCHMARK_UNAVAILABLE in findings[0].caveats


def test_missing_benchmark_with_no_set_pieces_uses_sentinel() -> None:
    findings = _compare(_setup(), None)
    assert len(findings) == 1
    assert CaveatCode.BENCHMARK_UNAVAILABLE in findings[0].caveats


def test_benchmark_with_zero_available_set_data() -> None:
    bm = _benchmark(_empty_bands())
    findings = _compare(_setup(10), bm)
    assert findings[0].observation is ObservationCode.MISSING_DATA
    assert CaveatCode.CATEGORY_UNAVAILABLE in findings[0].caveats
    assert findings[0].observation is not ObservationCode.LOW_PREVALENCE


def test_mismatched_target_raises() -> None:
    other_target = EncounterBenchmarkTarget(SpecId("Warlock", "Destruction"), 1234, 5, 34)
    with pytest.raises(SetupSetBonusComparisonError):
        compare_set_bonus(
            target=other_target, policy=_POLICY, player_setup=_setup(10), benchmark=_BM
        )


# -- evidence / actionable ---------------------------------------------------------------


def test_insufficient_evidence_never_actionable() -> None:
    policy = BenchmarkPolicy(min_sample_size=50)
    bm = _single_band_benchmark({"10": 3, "20": 1}, 4)
    findings = compare_set_bonus(
        target=_TARGET, policy=policy, player_setup=_setup(10), benchmark=bm
    )
    assert findings[0].evidence_level in (EvidenceLevel.INSUFFICIENT, EvidenceLevel.WEAK)
    assert findings[0].observation is ObservationCode.INSUFFICIENT_EVIDENCE
    assert findings[0].actionable is False


def test_partial_coverage_caveat() -> None:
    bm = _single_band_benchmark({"10": 36}, 72, sample_size=100)
    findings = _compare(_setup(10), bm)
    assert CaveatCode.PARTIAL_SETUP_COVERAGE in findings[0].caveats


# -- custom bands / determinism ----------------------------------------------------------


def test_custom_policy_bands() -> None:
    custom_policy = BenchmarkPolicy(
        bands=(PercentileBand("elite", 90.0, 100.0), PercentileBand("rest", 0.0, 90.0)),
        min_sample_size=4,
    )
    bands = {
        "elite": _band("elite", counts={"10": 9, "20": 1}, n_available=10),
        "rest": _band("rest", counts={"10": 5, "20": 5}, n_available=10),
    }
    bm = _benchmark(bands, policy=custom_policy)
    findings = _compare(_setup(20), bm, policy=custom_policy)
    assert findings[0].prevalence is not None
    band_names = {bp.band.name for bp in findings[0].prevalence.bands}
    assert band_names == {"elite", "rest"}
    assert findings[0].prevalence.count == 6 and findings[0].prevalence.n_available == 20


def test_deterministic_tie_break_by_key() -> None:
    bm = _single_band_benchmark({"10": 20, "20": 20}, 40)
    finding_10 = _compare(_setup(10), bm)[0]
    finding_20 = _compare(_setup(20), bm)[0]
    assert finding_10.observation is ObservationCode.MATCHES_COMMON_PATTERN
    assert finding_20.observation is ObservationCode.DIFFERS_FROM_COMMON_PATTERN


def test_finding_id_stable_across_calls() -> None:
    a = _compare(_setup(10), _BM)[0]
    b = _compare(_setup(10), _BM)[0]
    assert a.finding_id == b.finding_id


# -- legacy bug audit: never sums distinct set_ids together -----------------------------


def test_never_sums_distinct_set_ids_together() -> None:
    """Auditoria do bug legado `count_tier_pieces` (soma setIDs distintos):
    2 sets diferentes no benchmark, cada um com prevalência PRÓPRIA, nunca
    combinada numa contagem única.
    """
    bm = _single_band_benchmark({"10": 40, "20": 10}, 50)
    finding_10 = _compare(_setup(10), bm)[0]
    finding_20 = _compare(_setup(20), bm)[0]
    assert finding_10.prevalence is not None and finding_10.prevalence.count == 40
    assert finding_20.prevalence is not None and finding_20.prevalence.count == 10
    # nunca 50 (soma dos dois) em nenhum dos dois findings
    assert finding_10.prevalence.count != 50
    assert finding_20.prevalence.count != 50


def test_module_never_reads_legacy_count_tier_pieces() -> None:
    """`count_tier_pieces`/`tier_pieces` só aparecem na PROSA do docstring
    do módulo, explicando por que o bug legado não se aplica aqui — nunca
    como um símbolo/import/chamada real (checado via AST, não substring).
    """
    tree = ast.parse(inspect.getsource(setup_setbonus_module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            assert node.id not in ("count_tier_pieces", "tier_pieces")
        if isinstance(node, ast.Attribute):
            assert node.attr != "tier_pieces"
        if isinstance(node, ast.ImportFrom):
            assert all(alias.name != "count_tier_pieces" for alias in node.names)
    assert not hasattr(setup_setbonus_module, "count_tier_pieces")


# -- no invented 2pc/4pc bonus claims -----------------------------------------------------


def test_no_invented_2pc_4pc_bonus_claims() -> None:
    """Nenhuma dessas palavras aparece como SÍMBOLO real (a docstring do
    módulo as cita em prosa só para explicar por que são evitadas).
    """
    for banned in ("bonus_active", "set_bonus_tier", "two_piece", "four_piece"):
        assert not hasattr(setup_setbonus_module, banned)
    findings = _compare(_setup(10), _BM)
    for f in findings:
        assert not hasattr(f, "bonus_active")
        assert not hasattr(f, "piece_threshold")


def test_no_causal_or_bis_language() -> None:
    source = inspect.getsource(setup_setbonus_module)
    for banned in ("best", "optimal", "upgrade", "bis", "you should"):
        assert banned not in source.lower()


def test_honesty_zero_prevalence_never_becomes_causal() -> None:
    from botgitgud.analysis.setup_finding import render_observation, validate_setup_language

    findings = _compare(_setup(999), _BM)
    assert findings[0].observation is ObservationCode.LOW_PREVALENCE
    text = render_observation(ObservationCode.LOW_PREVALENCE)
    validate_setup_language(text)
    for forbidden in ("bad set", "wrong set", "replace this set"):
        assert forbidden not in text.lower()


# -- no SetupScore / no estimated_gain_pct -------------------------------------------------


def test_no_setup_score_or_estimated_gain() -> None:
    source = inspect.getsource(setup_setbonus_module)
    assert "estimated_gain_pct=" not in source
    for banned in ("SetupScore", "setup_score", "overall_setup_grade", "setup_rating"):
        assert not hasattr(setup_setbonus_module, banned)


# -- zero Execution Cohort / WCL / Discord --------------------------------------------------


def test_zero_execution_cohort_wcl_discord_store_job_imports() -> None:
    tree = ast.parse(inspect.getsource(setup_setbonus_module))
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

    source = inspect.getsource(setup_setbonus_module)
    assert "open(" not in source
    assert "Path(" not in source
