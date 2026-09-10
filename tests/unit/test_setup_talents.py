from __future__ import annotations

import ast
import inspect
import random

import pytest

from botgitgud.analysis import setup_talents as setup_talents_module
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
    talent_build_key,
)
from botgitgud.analysis.setup_finding import (
    CaveatCode,
    EvidenceLevel,
    ObservationCode,
    Publicability,
    SetupFinding,
)
from botgitgud.analysis.setup_talents import compare_talent_build
from botgitgud.domain.models import SetupProfile, TalentNode
from botgitgud.domain.specs import SpecId

# -- fixtures / helpers ---------------------------------------------------------

_TARGET = EncounterBenchmarkTarget(SpecId("Warlock", "Demonology"), 1234, 5, 34)
_POLICY = BenchmarkPolicy.default()  # p95-99, p75-95, p50-75

_EMPTY_STATS = DescriptiveStats(n=0, median=None, p25=None, p75=None)
_EMPTY_DIST = PrevalenceDistribution(n_available=0, entries=())
_EMPTY_SET = SetSummary(n_available=0, entries=())

_GOOD_SETUP = SetupProfile(talents=(TalentNode(node_id=1, rank=1, spell_id=999),))
_BAD_SETUP = SetupProfile(talents=(TalentNode(node_id=2, rank=2, spell_id=888),))


def _require_key(setup: SetupProfile) -> str:
    key = talent_build_key(setup)
    assert key is not None
    return key


GOOD = _require_key(_GOOD_SETUP)
BAD = _require_key(_BAD_SETUP)


def _prevalence_dist(counts: dict[str, int], n_available: int) -> PrevalenceDistribution:
    entries = tuple(
        PrevalenceEntry(key=k, n_observed=v, prevalence=(v / n_available if n_available else 0.0))
        for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    return PrevalenceDistribution(n_available=n_available, entries=entries)


def _band(
    name: str, counts: dict[str, int], n_available: int, *, sample_size: int | None = None
) -> BandBenchmark:
    ss = sample_size if sample_size is not None else n_available
    return BandBenchmark(
        band_name=name,
        status="ok" if ss >= 8 else "insufficient",
        sample_size=ss,
        raw_observation_count=ss,
        talent_build_prevalence=_prevalence_dist(counts, n_available),
        trinket_prevalence=_EMPTY_DIST,
        trinket_pair_prevalence=_EMPTY_DIST,
        set_summary=_EMPTY_SET,
        secondary_stats={s: _EMPTY_STATS for s in CANONICAL_SECONDARY_STATS},
        duration_summary=_EMPTY_STATS,
        item_level_summary=_EMPTY_STATS,
    )


def _empty_bands(policy: BenchmarkPolicy = _POLICY) -> dict[str, BandBenchmark]:
    return {b.name: _band(b.name, {}, 0) for b in policy.bands}


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
    counts: dict[str, int], n_available: int, *, band_name: str | None = None
) -> EncounterBenchmark:
    name = band_name or _POLICY.bands[0].name
    bands = _empty_bands()
    bands[name] = _band(name, counts, n_available)
    return _benchmark(bands)


def _multi_band_benchmark() -> EncounterBenchmark:
    bands = _empty_bands()
    bands.update(
        {
            "p95-99": _band("p95-99", {GOOD: 8, BAD: 2}, 10),
            "p75-95": _band("p75-95", {GOOD: 20, BAD: 10}, 30),
            "p50-75": _band("p50-75", {GOOD: 12, BAD: 8}, 20),
        }
    )
    return _benchmark(bands)


def _compare(
    setup: SetupProfile | None,
    benchmark: EncounterBenchmark | None,
    *,
    policy: BenchmarkPolicy = _POLICY,
    target: EncounterBenchmarkTarget = _TARGET,
) -> SetupFinding:
    return compare_talent_build(
        target=target, policy=policy, player_setup=setup, benchmark=benchmark
    )[0]


# -- 1-4: observation mapping ----------------------------------------------------


def test_exact_player_build_found() -> None:
    bm = _single_band_benchmark({GOOD: 40, BAD: 10}, 50)
    finding = _compare(_BAD_SETUP, bm)
    assert finding.subject.talent_fingerprint == BAD
    assert finding.prevalence is not None and finding.prevalence.count == 10


def test_common_build_matches_common_pattern() -> None:
    bm = _single_band_benchmark({GOOD: 40, BAD: 10}, 50)
    finding = _compare(_GOOD_SETUP, bm)
    assert finding.observation is ObservationCode.MATCHES_COMMON_PATTERN
    assert finding.actionable is False


def test_present_but_uncommon_build_differs_from_common_pattern() -> None:
    bm = _single_band_benchmark({GOOD: 40, BAD: 10}, 50)
    finding = _compare(_BAD_SETUP, bm)
    assert finding.observation is ObservationCode.DIFFERS_FROM_COMMON_PATTERN
    assert finding.actionable is True


def test_unobserved_build_is_low_prevalence() -> None:
    bm = _single_band_benchmark({GOOD: 40, BAD: 10}, 50)
    unseen = SetupProfile(talents=(TalentNode(node_id=99, rank=1, spell_id=1),))
    finding = _compare(unseen, bm)
    assert finding.observation is ObservationCode.LOW_PREVALENCE
    assert finding.prevalence is not None and finding.prevalence.count == 0
    assert finding.actionable is True


# -- 5-8: denominators / aggregation ----------------------------------------------


def test_prevalence_uses_talent_available_not_band_sample_size() -> None:
    """band sample_size=100, talent build available=72, player count=36 ->
    prevalence correta é 36/72, NUNCA 36/100."""
    bands = _empty_bands()
    bands["p50-75"] = _band("p50-75", {GOOD: 36, BAD: 36}, 72, sample_size=100)
    bm = _benchmark(bands)
    finding = _compare(_GOOD_SETUP, bm)
    assert finding.prevalence is not None
    assert finding.prevalence.n_available == 72
    assert finding.prevalence.count == 36
    assert finding.prevalence.prevalence == pytest.approx(0.5)


def test_does_not_use_band_sample_size_as_denominator() -> None:
    bands = _empty_bands()
    bands["p50-75"] = _band("p50-75", {GOOD: 36, BAD: 36}, 72, sample_size=100)
    bm = _benchmark(bands)
    finding = _compare(_GOOD_SETUP, bm)
    assert finding.prevalence is not None
    assert finding.prevalence.n_available != 100


def test_aggregation_weighs_by_count_over_n_available() -> None:
    bm = _multi_band_benchmark()
    finding = _compare(_BAD_SETUP, bm)
    assert finding.prevalence is not None
    assert finding.prevalence.count == 20
    assert finding.prevalence.n_available == 60
    assert finding.prevalence.prevalence == pytest.approx(20 / 60)


def test_aggregation_is_not_a_simple_mean_of_band_prevalences() -> None:
    """p95: 8/10=80%; p75: 10/40=25% -> global correto é 18/50=36%, nunca
    a média simples (80%+25%)/2 = 52.5%."""
    bands = _empty_bands()
    bands["p95-99"] = _band("p95-99", {GOOD: 2, BAD: 8}, 10)
    bands["p75-95"] = _band("p75-95", {GOOD: 30, BAD: 10}, 40)
    bm = _benchmark(bands)
    finding = _compare(_BAD_SETUP, bm)
    assert finding.prevalence is not None
    assert finding.prevalence.count == 18
    assert finding.prevalence.n_available == 50
    assert finding.prevalence.prevalence == pytest.approx(0.36)
    assert finding.prevalence.prevalence != pytest.approx((0.8 + 0.25) / 2)


# -- 9-11: band breakdown / customização ------------------------------------------


def test_band_breakdown_is_correct_per_band() -> None:
    bm = _multi_band_benchmark()
    finding = _compare(_BAD_SETUP, bm)
    assert finding.prevalence is not None
    breakdown = {bp.band.name: (bp.n_available, bp.prevalence) for bp in finding.prevalence.bands}
    assert breakdown["p95-99"] == (10, pytest.approx(2 / 10))
    assert breakdown["p75-95"] == (30, pytest.approx(10 / 30))
    assert breakdown["p50-75"] == (20, pytest.approx(8 / 20))


def test_works_with_custom_policy_bands() -> None:
    custom_policy = BenchmarkPolicy(
        bands=(PercentileBand("elite", 90.0, 100.0), PercentileBand("rest", 0.0, 90.0)),
        min_sample_size=4,
    )
    bands = {
        "elite": _band("elite", {GOOD: 9, BAD: 1}, 10),
        "rest": _band("rest", {GOOD: 5, BAD: 5}, 10),
    }
    bm = _benchmark(bands, policy=custom_policy)
    finding = compare_talent_build(
        target=_TARGET, policy=custom_policy, player_setup=_BAD_SETUP, benchmark=bm
    )[0]
    assert finding.prevalence is not None
    band_names = {bp.band.name for bp in finding.prevalence.bands}
    assert band_names == {"elite", "rest"}
    assert finding.prevalence.count == 6 and finding.prevalence.n_available == 20


def test_band_order_does_not_change_the_result() -> None:
    """`BenchmarkPolicy.__post_init__` (EB.1) já reordena `bands` por `low`
    não importa a ordem de construção — constrói a policy com as bandas em
    ordem invertida e confirma que o resultado de SA.2 é idêntico.
    """
    bm = _multi_band_benchmark()
    reordered_policy = BenchmarkPolicy(
        bands=tuple(reversed(_POLICY.bands)), min_sample_size=_POLICY.min_sample_size
    )
    assert reordered_policy.bands == _POLICY.bands  # já canônico após __post_init__
    a = _compare(_BAD_SETUP, bm)
    b = _compare(_BAD_SETUP, bm, policy=reordered_policy)
    assert a == b


# -- 12-14: common pattern determinism --------------------------------------------


def test_common_build_is_deterministic() -> None:
    bm = _single_band_benchmark({GOOD: 40, BAD: 10}, 50)
    a = _compare(_GOOD_SETUP, bm)
    b = _compare(_GOOD_SETUP, bm)
    assert a.observation is b.observation is ObservationCode.MATCHES_COMMON_PATTERN


def test_tie_has_canonical_tiebreak_by_key() -> None:
    key_a, key_b = sorted([GOOD, BAD])  # key_a < key_b lexicographically
    bm = _single_band_benchmark({key_a: 20, key_b: 20}, 40)
    setup_a = _GOOD_SETUP if key_a == GOOD else _BAD_SETUP
    setup_b = _GOOD_SETUP if key_b == GOOD else _BAD_SETUP
    finding_a = _compare(setup_a, bm)
    finding_b = _compare(setup_b, bm)
    # o build com a MENOR key vence o empate -> vira o "common pattern"
    assert finding_a.observation is ObservationCode.MATCHES_COMMON_PATTERN
    assert finding_b.observation is ObservationCode.DIFFERS_FROM_COMMON_PATTERN


def test_entry_order_does_not_change_common_build() -> None:
    """Constrói a mesma distribuição com entries em ordens diferentes —
    `PrevalenceDistribution.entries` já é canônica (EB.2), mas este teste
    garante que SA.2 não depende de mais nenhuma ordem além dela.
    """
    n_available = 50
    entries_a = (
        PrevalenceEntry(GOOD, 40, 40 / 50),
        PrevalenceEntry(BAD, 10, 10 / 50),
    )
    entries_b = (
        PrevalenceEntry(BAD, 10, 10 / 50),
        PrevalenceEntry(GOOD, 40, 40 / 50),
    )
    band_a = BandBenchmark(
        band_name="p50-75",
        status="ok",
        sample_size=n_available,
        raw_observation_count=n_available,
        talent_build_prevalence=PrevalenceDistribution(n_available=n_available, entries=entries_a),
        trinket_prevalence=_EMPTY_DIST,
        trinket_pair_prevalence=_EMPTY_DIST,
        set_summary=_EMPTY_SET,
        secondary_stats={s: _EMPTY_STATS for s in CANONICAL_SECONDARY_STATS},
        duration_summary=_EMPTY_STATS,
        item_level_summary=_EMPTY_STATS,
    )
    band_b = BandBenchmark(
        band_name="p50-75",
        status="ok",
        sample_size=n_available,
        raw_observation_count=n_available,
        talent_build_prevalence=PrevalenceDistribution(n_available=n_available, entries=entries_b),
        trinket_prevalence=_EMPTY_DIST,
        trinket_pair_prevalence=_EMPTY_DIST,
        set_summary=_EMPTY_SET,
        secondary_stats={s: _EMPTY_STATS for s in CANONICAL_SECONDARY_STATS},
        duration_summary=_EMPTY_STATS,
        item_level_summary=_EMPTY_STATS,
    )
    bands_a = _empty_bands()
    bands_a["p50-75"] = band_a
    bands_b = _empty_bands()
    bands_b["p50-75"] = band_b
    bm_a = _benchmark(bands_a)
    bm_b = _benchmark(bands_b)
    finding_a = _compare(_GOOD_SETUP, bm_a)
    finding_b = _compare(_GOOD_SETUP, bm_b)
    assert finding_a == finding_b


# -- 15-16: identidade de build ---------------------------------------------------


def test_spell_id_does_not_change_identity() -> None:
    setup_1 = SetupProfile(talents=(TalentNode(node_id=5, rank=2, spell_id=111),))
    setup_2 = SetupProfile(talents=(TalentNode(node_id=5, rank=2, spell_id=222),))
    bm = _single_band_benchmark({_require_key(setup_1): 10}, 10)
    finding_1 = _compare(setup_1, bm)
    finding_2 = _compare(setup_2, bm)
    assert finding_1.subject == finding_2.subject
    assert finding_1.observation is finding_2.observation is ObservationCode.MATCHES_COMMON_PATTERN


def test_node_id_rank_changes_identity() -> None:
    setup_1 = SetupProfile(talents=(TalentNode(node_id=5, rank=2, spell_id=111),))
    setup_2 = SetupProfile(talents=(TalentNode(node_id=5, rank=3, spell_id=111),))
    assert talent_build_key(setup_1) != talent_build_key(setup_2)
    bm = _single_band_benchmark({_require_key(setup_1): 10}, 10)
    finding_2 = _compare(setup_2, bm)
    assert finding_2.observation is ObservationCode.LOW_PREVALENCE


# -- 17-20: missing data -----------------------------------------------------------


def test_missing_player_setup() -> None:
    bm = _single_band_benchmark({GOOD: 10}, 10)
    finding = _compare(None, bm)
    assert finding.observation is ObservationCode.MISSING_DATA
    assert CaveatCode.PLAYER_SETUP_MISSING in finding.caveats
    assert finding.publicability is Publicability.HIDDEN


def test_missing_talents_on_present_setup() -> None:
    bm = _single_band_benchmark({GOOD: 10}, 10)
    empty_setup = SetupProfile(talents=())
    finding = _compare(empty_setup, bm)
    assert finding.observation is ObservationCode.MISSING_DATA
    assert CaveatCode.CATEGORY_UNAVAILABLE in finding.caveats


def test_missing_benchmark() -> None:
    finding = _compare(_GOOD_SETUP, None)
    assert finding.observation is ObservationCode.MISSING_DATA
    assert CaveatCode.BENCHMARK_UNAVAILABLE in finding.caveats
    # subject usa a key REAL do jogador (resolvível mesmo sem benchmark)
    assert finding.subject.talent_fingerprint == GOOD


def test_benchmark_with_zero_available_talent_data() -> None:
    bm = _benchmark(_empty_bands())
    finding = _compare(_GOOD_SETUP, bm)
    assert finding.observation is ObservationCode.MISSING_DATA
    assert CaveatCode.CATEGORY_UNAVAILABLE in finding.caveats
    # NUNCA LOW_PREVALENCE quando o denominador é zero
    assert finding.observation is not ObservationCode.LOW_PREVALENCE


# -- 21-23: caveats ----------------------------------------------------------------


def test_partial_setup_coverage_caveat() -> None:
    bands = _empty_bands()
    bands["p50-75"] = _band("p50-75", {GOOD: 36}, 72, sample_size=100)
    bm = _benchmark(bands)
    finding = _compare(_GOOD_SETUP, bm)
    assert CaveatCode.PARTIAL_SETUP_COVERAGE in finding.caveats


def test_no_partial_coverage_caveat_when_denominators_match() -> None:
    bm = _single_band_benchmark({GOOD: 40, BAD: 10}, 50)  # sample_size == n_available
    finding = _compare(_GOOD_SETUP, bm)
    assert CaveatCode.PARTIAL_SETUP_COVERAGE not in finding.caveats


def test_talent_names_unresolved_caveat_always_present_on_real_comparisons() -> None:
    bm = _single_band_benchmark({GOOD: 40, BAD: 10}, 50)
    finding = _compare(_GOOD_SETUP, bm)
    assert CaveatCode.TALENT_NAMES_UNRESOLVED in finding.caveats


def test_observational_only_caveat_always_present() -> None:
    bm = _single_band_benchmark({GOOD: 40, BAD: 10}, 50)
    finding = _compare(_GOOD_SETUP, bm)
    assert CaveatCode.OBSERVATIONAL_ONLY in finding.caveats


# -- 24-25: evidence / publicability reuse ------------------------------------------


def test_evidence_uses_talent_denominator_not_band_sample_size() -> None:
    """min_sample_size=50; band.sample_size=100 mas talent available=40 ->
    WEAK correto (40<50); usar sample_size=100 daria MODERATE por engano."""
    policy = BenchmarkPolicy(min_sample_size=50)
    bands = _empty_bands(policy)
    bands["p50-75"] = _band("p50-75", {GOOD: 30, BAD: 10}, 40, sample_size=100)
    bm = _benchmark(bands, policy=policy)
    finding = _compare(_GOOD_SETUP, bm, policy=policy)
    assert finding.evidence_level is EvidenceLevel.WEAK


def test_publicability_reuses_sa1_rule() -> None:
    policy = BenchmarkPolicy(min_sample_size=50)
    bands = _empty_bands(policy)
    bands["p50-75"] = _band("p50-75", {GOOD: 3, BAD: 1}, 4)
    bm = _benchmark(bands, policy=policy)
    finding = _compare(_GOOD_SETUP, bm, policy=policy)
    assert finding.evidence_level is EvidenceLevel.WEAK
    assert finding.publicability is Publicability.CAUTION


# -- 26-28: actionable --------------------------------------------------------------


def test_insufficient_evidence_is_never_actionable() -> None:
    policy = BenchmarkPolicy(min_sample_size=50)
    bands = _empty_bands(policy)
    bands["p50-75"] = _band("p50-75", {GOOD: 3, BAD: 1}, 4)
    bm = _benchmark(bands, policy=policy)
    finding = _compare(_GOOD_SETUP, bm, policy=policy)
    assert finding.observation is ObservationCode.INSUFFICIENT_EVIDENCE
    assert finding.actionable is False


def test_uncommon_build_with_sufficient_evidence_can_be_actionable() -> None:
    bm = _single_band_benchmark({GOOD: 40, BAD: 10}, 50)
    finding = _compare(_BAD_SETUP, bm)
    assert finding.evidence_level in (EvidenceLevel.MODERATE, EvidenceLevel.STRONG)
    assert finding.actionable is True


def test_actionable_never_implies_a_causal_recommendation() -> None:
    bm = _single_band_benchmark({GOOD: 40, BAD: 10}, 50)
    finding = _compare(_BAD_SETUP, bm)
    assert finding.actionable is True
    assert not hasattr(finding, "estimated_gain_pct")


# -- 29-30: cenário 10 BAD / 40 GOOD, não-circularidade -----------------------------


def test_scenario_10_bad_40_good_does_not_filter_benchmark() -> None:
    bm = _single_band_benchmark({GOOD: 40, BAD: 10}, 50)

    bad_finding = _compare(_BAD_SETUP, bm)
    good_finding = _compare(_GOOD_SETUP, bm)

    # o denominador do benchmark é o MESMO independente de qual jogador
    # está sendo analisado — a prova direta de não-circularidade.
    assert bad_finding.prevalence is not None and good_finding.prevalence is not None
    assert bad_finding.prevalence.n_available == good_finding.prevalence.n_available == 50

    assert bad_finding.prevalence.count == 10  # NUNCA 10/10 = 100%
    assert bad_finding.prevalence.prevalence == pytest.approx(0.2)
    assert bad_finding.observation is ObservationCode.DIFFERS_FROM_COMMON_PATTERN

    # o benchmark original (as duas contagens) nunca é mutado pela chamada
    band = bm.bands[_POLICY.bands[0].name]
    counts = {e.key: e.n_observed for e in band.talent_build_prevalence.entries}
    assert counts == {GOOD: 40, BAD: 10}


def test_does_not_filter_benchmark_by_player_build() -> None:
    """Regression arquitetural: comparar o jogador BAD nunca reduz o
    benchmark a só observações BAD."""
    bm = _single_band_benchmark({GOOD: 40, BAD: 10}, 50)
    finding = _compare(_BAD_SETUP, bm)
    assert finding.prevalence is not None
    assert finding.prevalence.n_available == 50  # não 10 (o subconjunto BAD)


# -- 31-33: multi-band 20 BAD / 40 GOOD ----------------------------------------------


def test_scenario_multi_band_aggregate_is_20_over_60() -> None:
    bm = _multi_band_benchmark()
    finding = _compare(_BAD_SETUP, bm)
    assert finding.prevalence is not None
    assert finding.prevalence.count == 20
    assert finding.prevalence.n_available == 60


def test_scenario_multi_band_common_pattern_is_good() -> None:
    bm = _multi_band_benchmark()
    finding = _compare(_GOOD_SETUP, bm)
    assert finding.observation is ObservationCode.MATCHES_COMMON_PATTERN


def test_scenario_multi_band_breakdown_exact() -> None:
    bm = _multi_band_benchmark()
    finding = _compare(_BAD_SETUP, bm)
    assert finding.prevalence is not None
    breakdown = {bp.band.name: bp.n_available for bp in finding.prevalence.bands}
    assert breakdown == {"p99-100": 0, "p95-99": 10, "p75-95": 30, "p50-75": 20}


# -- 34-35: determinismo -------------------------------------------------------------


def test_finding_id_is_stable_across_calls() -> None:
    bm = _multi_band_benchmark()
    a = _compare(_BAD_SETUP, bm)
    b = _compare(_BAD_SETUP, bm)
    assert a.finding_id == b.finding_id


def test_output_ordering_deterministic_regardless_of_band_dict_construction_order() -> None:
    bands_forward = {
        "p95-99": _band("p95-99", {GOOD: 8, BAD: 2}, 10),
        "p75-95": _band("p75-95", {GOOD: 20, BAD: 10}, 30),
        "p50-75": _band("p50-75", {GOOD: 12, BAD: 8}, 20),
    }
    bands_reversed = {
        "p50-75": bands_forward["p50-75"],
        "p75-95": bands_forward["p75-95"],
        "p95-99": bands_forward["p95-99"],
    }
    bm_a = _benchmark(bands_forward)
    bm_b = _benchmark(bands_reversed)
    a = _compare(_BAD_SETUP, bm_a)
    b = _compare(_BAD_SETUP, bm_b)
    assert a == b
    assert a.prevalence == b.prevalence


def test_deterministic_under_random_seed() -> None:
    random.seed(1234)
    bm = _multi_band_benchmark()
    results = set()
    for _ in range(5):
        finding = _compare(_BAD_SETUP, bm)
        results.add((finding.finding_id, finding.observation))
    assert len(results) == 1


# -- 36-38: sem causalidade -----------------------------------------------------------


def test_no_setup_score() -> None:
    source = inspect.getsource(setup_talents_module)
    for banned in ("SetupScore", "setup_score", "overall_setup_grade", "setup_rating"):
        assert not hasattr(setup_talents_module, banned)
        assert banned not in source


def test_no_estimated_gain_pct() -> None:
    """`estimated_gain_pct` só é banido como SÍMBOLO/campo real — a
    docstring do módulo cita o nome em prosa para explicar por que não
    existe (mesma distinção de `test_no_setup_score`).
    """
    source = inspect.getsource(setup_talents_module)
    assert "estimated_gain_pct=" not in source
    assert "estimated_gain_pct:" not in source
    bm = _single_band_benchmark({GOOD: 40, BAD: 10}, 50)
    finding = _compare(_BAD_SETUP, bm)
    assert not hasattr(finding, "estimated_gain_pct")


def test_no_dps_causal_language_in_module_source() -> None:

    source = inspect.getsource(setup_talents_module)
    # qualquer string literal do módulo precisa passar pelo guard —
    # aplicado às docstrings/comentários relevantes manualmente aqui,
    # já que o guard oficial é escopado a texto que um SetupFinding
    # efetivamente carrega (setup_finding.py), não a comentários de código.
    for forbidden in ("+5% dps", "gain 5%", "this costs", "replace this build"):
        assert forbidden not in source.lower()


# -- 39-42: zero acoplamento com Execution Cohort / runtime ---------------------------


def test_zero_execution_cohort_wcl_discord_store_job_imports() -> None:
    tree = ast.parse(inspect.getsource(setup_talents_module))
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

    source = inspect.getsource(setup_talents_module)
    assert "open(" not in source
    assert "Path(" not in source


# -- teste de honestidade -------------------------------------------------------------


def test_honesty_zero_prevalence_stays_observational_never_causal() -> None:
    from botgitgud.analysis.setup_finding import render_observation, validate_setup_language

    bm = _single_band_benchmark({GOOD: 50}, 50)  # BAD never observed
    finding = _compare(_BAD_SETUP, bm)
    assert finding.prevalence is not None and finding.prevalence.prevalence == 0.0
    assert finding.observation is ObservationCode.LOW_PREVALENCE

    text = render_observation(ObservationCode.LOW_PREVALENCE)
    validate_setup_language(text)  # não levanta — vocabulário observacional

    forbidden_phrases = (
        "bad build",
        "wrong build",
        "worse build",
        "replace this build",
        "this costs",
    )
    for forbidden in forbidden_phrases:
        assert forbidden not in text.lower()
