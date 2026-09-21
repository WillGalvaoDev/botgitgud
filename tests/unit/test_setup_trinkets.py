from __future__ import annotations

import ast
import inspect

import pytest

from botgitgud.analysis import setup_trinkets as setup_trinkets_module
from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget, PercentileBand
from botgitgud.analysis.benchmark_aggregate import (
    CANONICAL_SECONDARY_STATS,
    BandBenchmark,
    CoverageSummary,
    DescriptiveStats,
    EncounterBenchmark,
    PrevalenceDistribution,
    PrevalenceEntry,
    SetSummary,
)
from botgitgud.analysis.setup_finding import (
    CaveatCode,
    EvidenceLevel,
    FindingCategory,
    ObservationCode,
    Publicability,
    SetupFinding,
)
from botgitgud.analysis.setup_trinkets import SetupTrinketComparisonError, compare_trinkets
from botgitgud.domain.models import GearPiece, SetupProfile
from botgitgud.domain.specs import SpecId

_TARGET = EncounterBenchmarkTarget(SpecId("Warlock", "Demonology"), 1234, 5, 34)
_POLICY = BenchmarkPolicy.default()

_EMPTY_STATS = DescriptiveStats(n=0, median=None, p25=None, p75=None)
_EMPTY_DIST = PrevalenceDistribution(n_available=0, entries=())
_EMPTY_SET = SetSummary(n_available=0, entries=())


def _dist(counts: dict[str, int], n_available: int) -> PrevalenceDistribution:
    entries = tuple(
        PrevalenceEntry(key=k, n_observed=v, prevalence=(v / n_available if n_available else 0.0))
        for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    return PrevalenceDistribution(n_available=n_available, entries=entries)


def _band(
    name: str,
    *,
    trinket_counts: dict[str, int] | None = None,
    trinket_n: int = 0,
    pair_counts: dict[str, int] | None = None,
    pair_n: int = 0,
    sample_size: int | None = None,
) -> BandBenchmark:
    ss = sample_size if sample_size is not None else max(trinket_n, pair_n)
    return BandBenchmark(
        band_name=name,
        status="ok" if ss >= 8 else "insufficient",
        sample_size=ss,
        raw_observation_count=ss,
        talent_build_prevalence=_EMPTY_DIST,
        trinket_prevalence=_dist(trinket_counts or {}, trinket_n),
        trinket_pair_prevalence=_dist(pair_counts or {}, pair_n),
        set_summary=_EMPTY_SET,
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
    *,
    trinket_counts: dict[str, int],
    trinket_n: int,
    pair_counts: dict[str, int],
    pair_n: int,
    sample_size: int | None = None,
) -> EncounterBenchmark:
    name = _POLICY.bands[0].name
    bands = _empty_bands()
    bands[name] = _band(
        name,
        trinket_counts=trinket_counts,
        trinket_n=trinket_n,
        pair_counts=pair_counts,
        pair_n=pair_n,
        sample_size=sample_size,
    )
    return _benchmark(bands)


def _compare(
    setup: SetupProfile | None,
    benchmark: EncounterBenchmark | None,
    *,
    policy: BenchmarkPolicy = _POLICY,
    target: EncounterBenchmarkTarget = _TARGET,
) -> tuple[SetupFinding, ...]:
    return compare_trinkets(target=target, policy=policy, player_setup=setup, benchmark=benchmark)


def _setup(*item_ids: int) -> SetupProfile:
    slots = (12, 13)
    gear = tuple(GearPiece(slot=slots[i], item_id=iid) for i, iid in enumerate(item_ids))
    return SetupProfile(gear=gear)


def _items(findings: tuple[SetupFinding, ...]) -> tuple[SetupFinding, ...]:
    return tuple(f for f in findings if f.category is FindingCategory.TRINKET)


def _pair(findings: tuple[SetupFinding, ...]) -> SetupFinding:
    matches = [f for f in findings if f.category is FindingCategory.TRINKET_PAIR]
    assert len(matches) == 1
    return matches[0]


# -- benchmark fixture: item 100 common (40/50), item 200 uncommon (10/50) -------
# pair 100+300 common (30/50), pair 200+300 uncommon (5/50)

_BM = _single_band_benchmark(
    trinket_counts={"100": 40, "200": 10},
    trinket_n=50,
    pair_counts={"100+300": 30, "200+300": 5},
    pair_n=50,
)


# -- individual common/uncommon --------------------------------------------------


def test_individual_trinket_common() -> None:
    findings = _compare(_setup(100), _BM)
    item = _items(findings)[0]
    assert item.observation is ObservationCode.MATCHES_COMMON_PATTERN
    assert item.actionable is False


def test_individual_trinket_uncommon() -> None:
    findings = _compare(_setup(200), _BM)
    item = _items(findings)[0]
    assert item.observation is ObservationCode.DIFFERS_FROM_COMMON_PATTERN
    assert item.actionable is True


def test_two_trinkets_independently_common_and_uncommon() -> None:
    findings = _compare(_setup(100, 200), _BM)
    items = {f.subject.item_id: f.observation for f in _items(findings)}
    assert items[100] is ObservationCode.MATCHES_COMMON_PATTERN
    assert items[200] is ObservationCode.DIFFERS_FROM_COMMON_PATTERN


# -- pair common/uncommon ---------------------------------------------------------


def test_pair_common() -> None:
    findings = _compare(_setup(100, 300), _BM)
    pair = _pair(findings)
    assert pair.observation is ObservationCode.MATCHES_COMMON_PATTERN


def test_pair_uncommon() -> None:
    findings = _compare(_setup(200, 300), _BM)
    pair = _pair(findings)
    assert pair.observation is ObservationCode.DIFFERS_FROM_COMMON_PATTERN


# -- swapped slot order -------------------------------------------------------------


def test_swapped_slot_order_is_the_same_pair_identity() -> None:
    setup_forward = _setup(100, 300)
    setup_swapped = SetupProfile(
        gear=(GearPiece(slot=12, item_id=300), GearPiece(slot=13, item_id=100))
    )
    pair_a = _pair(_compare(setup_forward, _BM))
    pair_b = _pair(_compare(setup_swapped, _BM))
    assert pair_a.subject == pair_b.subject
    assert pair_a.finding_id == pair_b.finding_id
    assert pair_a == pair_b


# -- missing one / missing both trinkets ---------------------------------------------


def test_missing_one_trinket_still_compares_the_present_one() -> None:
    findings = _compare(_setup(100), _BM)
    items = _items(findings)
    assert len(items) == 1
    assert items[0].subject.item_id == 100
    pair = _pair(findings)
    assert pair.observation is ObservationCode.MISSING_DATA
    assert CaveatCode.CATEGORY_UNAVAILABLE in pair.caveats


def test_missing_both_trinkets() -> None:
    findings = _compare(_setup(), _BM)
    items = _items(findings)
    assert len(items) == 1  # sentinela único, categoria indisponível
    assert items[0].observation is ObservationCode.MISSING_DATA
    assert CaveatCode.CATEGORY_UNAVAILABLE in items[0].caveats
    pair = _pair(findings)
    assert pair.observation is ObservationCode.MISSING_DATA


# -- partial coverage -----------------------------------------------------------------


def test_partial_coverage_caveat() -> None:
    bm = _single_band_benchmark(
        trinket_counts={"100": 36}, trinket_n=72, pair_counts={}, pair_n=0, sample_size=100
    )
    findings = _compare(_setup(100), bm)
    item = _items(findings)[0]
    assert CaveatCode.PARTIAL_SETUP_COVERAGE in item.caveats
    assert item.prevalence is not None
    assert item.prevalence.n_available == 72  # nunca 100 (sample_size)


def test_no_partial_coverage_when_denominators_match() -> None:
    findings = _compare(_setup(100), _BM)
    item = _items(findings)[0]
    assert CaveatCode.PARTIAL_SETUP_COVERAGE not in item.caveats


# -- custom bands -----------------------------------------------------------------------


def test_custom_policy_bands() -> None:
    custom_policy = BenchmarkPolicy(
        bands=(PercentileBand("elite", 90.0, 100.0), PercentileBand("rest", 0.0, 90.0)),
        min_sample_size=4,
    )
    bands = {
        "elite": _band("elite", trinket_counts={"100": 9, "200": 1}, trinket_n=10),
        "rest": _band("rest", trinket_counts={"100": 5, "200": 5}, trinket_n=10),
    }
    bm = _benchmark(bands, policy=custom_policy)
    findings = _compare(_setup(200), bm, policy=custom_policy)
    item = _items(findings)[0]
    assert item.prevalence is not None
    band_names = {bp.band.name for bp in item.prevalence.bands}
    assert band_names == {"elite", "rest"}
    assert item.prevalence.count == 6 and item.prevalence.n_available == 20


# -- deterministic ties -----------------------------------------------------------------


def test_deterministic_tie_break_by_key() -> None:
    # "100" < "200" lexicograficamente -> "100" vence o empate
    bm = _single_band_benchmark(
        trinket_counts={"100": 20, "200": 20}, trinket_n=40, pair_counts={}, pair_n=0
    )
    finding_100 = _items(_compare(_setup(100), bm))[0]
    finding_200 = _items(_compare(_setup(200), bm))[0]
    assert finding_100.observation is ObservationCode.MATCHES_COMMON_PATTERN
    assert finding_200.observation is ObservationCode.DIFFERS_FROM_COMMON_PATTERN


def test_tie_break_independent_of_band_dict_construction_order() -> None:
    bands_forward = {
        "p95-99": _band("p95-99", trinket_counts={"100": 8, "200": 8}, trinket_n=16),
        "p75-95": _band("p75-95", trinket_counts={}, trinket_n=0),
        "p50-75": _band("p50-75", trinket_counts={}, trinket_n=0),
    }
    bands_reversed = {
        "p50-75": bands_forward["p50-75"],
        "p75-95": bands_forward["p75-95"],
        "p95-99": bands_forward["p95-99"],
    }
    bm_a = _benchmark(bands_forward)
    bm_b = _benchmark(bands_reversed)
    a = _items(_compare(_setup(200), bm_a))[0]
    b = _items(_compare(_setup(200), bm_b))[0]
    assert a == b


# -- zero causal language ---------------------------------------------------------------


def test_zero_causal_language_in_module_source() -> None:
    source = inspect.getsource(setup_trinkets_module)
    for banned in ("best", "optimal", "upgrade", "bad item", "worse", "you should"):
        assert banned not in source.lower()


def test_honesty_zero_prevalence_never_becomes_causal() -> None:
    from botgitgud.analysis.setup_finding import render_observation, validate_setup_language

    bm = _single_band_benchmark(trinket_counts={"999": 50}, trinket_n=50, pair_counts={}, pair_n=0)
    finding = _items(_compare(_setup(100), bm))[0]
    assert finding.observation is ObservationCode.LOW_PREVALENCE
    assert finding.prevalence is not None and finding.prevalence.count == 0

    text = render_observation(ObservationCode.LOW_PREVALENCE)
    validate_setup_language(text)
    for forbidden in ("bad item", "upgrade", "wrong trinket", "replace this trinket"):
        assert forbidden not in text.lower()


# -- zero Execution Cohort dependency -----------------------------------------------------


def test_zero_execution_cohort_wcl_discord_store_job_imports() -> None:
    tree = ast.parse(inspect.getsource(setup_trinkets_module))
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

    source = inspect.getsource(setup_trinkets_module)
    assert "open(" not in source
    assert "Path(" not in source


def test_no_estimated_gain_pct_or_setup_score() -> None:
    source = inspect.getsource(setup_trinkets_module)
    assert "estimated_gain_pct=" not in source
    for banned in ("SetupScore", "setup_score", "overall_setup_grade", "setup_rating"):
        assert not hasattr(setup_trinkets_module, banned)


# -- missing player setup / benchmark ------------------------------------------------------


def test_missing_player_setup_produces_two_category_findings() -> None:
    findings = _compare(None, _BM)
    assert len(findings) == 2
    categories = {f.category for f in findings}
    assert categories == {FindingCategory.TRINKET, FindingCategory.TRINKET_PAIR}
    for f in findings:
        assert f.observation is ObservationCode.MISSING_DATA
        assert f.publicability is Publicability.HIDDEN


def test_missing_benchmark_uses_real_item_ids_as_subjects() -> None:
    findings = _compare(_setup(100, 300), None)
    items = _items(findings)
    assert {f.subject.item_id for f in items} == {100, 300}
    for f in findings:
        assert f.observation is ObservationCode.MISSING_DATA
        assert CaveatCode.BENCHMARK_UNAVAILABLE in f.caveats


def test_benchmark_with_zero_available_trinket_data() -> None:
    bm = _benchmark(_empty_bands())
    findings = _compare(_setup(100), bm)
    item = _items(findings)[0]
    assert item.observation is ObservationCode.MISSING_DATA
    assert CaveatCode.CATEGORY_UNAVAILABLE in item.caveats
    assert item.observation is not ObservationCode.LOW_PREVALENCE


def test_mismatched_target_raises() -> None:
    other_target = EncounterBenchmarkTarget(SpecId("Warlock", "Destruction"), 1234, 5, 34)
    with pytest.raises(SetupTrinketComparisonError):
        compare_trinkets(
            target=other_target, policy=_POLICY, player_setup=_setup(100), benchmark=_BM
        )


def test_evidence_insufficient_never_actionable() -> None:
    policy = BenchmarkPolicy(min_sample_size=50)
    bm = _single_band_benchmark(
        trinket_counts={"100": 3, "200": 1}, trinket_n=4, pair_counts={}, pair_n=0
    )
    findings = compare_trinkets(
        target=_TARGET, policy=policy, player_setup=_setup(100), benchmark=bm
    )
    item = _items(findings)[0]
    assert item.evidence_level in (EvidenceLevel.INSUFFICIENT, EvidenceLevel.WEAK)
    assert item.observation is ObservationCode.INSUFFICIENT_EVIDENCE
    assert item.actionable is False
