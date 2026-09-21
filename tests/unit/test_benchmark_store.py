"""EB.3 — persistência + staleness do Encounter Benchmark. Todo teste usa
`tmp_path` (nunca `data/` real); nenhuma rede.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget, PercentileBand
from botgitgud.analysis.benchmark_aggregate import EncounterBenchmark, build_encounter_benchmark
from botgitgud.analysis.benchmark_store import BenchmarkStore
from botgitgud.analysis.benchmark_store_models import (
    BenchmarkFreshnessPolicy,
    EncounterBenchmarkCorruptPayloadError,
    now_utc_naive,
    policy_fingerprint,
    population_fingerprint,
    population_growth_ratio,
)
from botgitgud.domain.models import FightRef, PlayerBuild, PlayerLog, SetupProfile, TalentNode
from botgitgud.domain.specs import SpecId
from botgitgud.ingest.store import Store

TARGET = EncounterBenchmarkTarget(
    spec=SpecId("Warlock", "Demonology"), encounter_id=3179, difficulty=5, partition=3
)


def _store(tmp_path: Path) -> tuple[BenchmarkStore, Store]:
    store = Store(tmp_path)
    return BenchmarkStore(store), store


def _log(
    name: str,
    percentile: float,
    *,
    target: EncounterBenchmarkTarget = TARGET,
    report_code: str | None = None,
) -> PlayerLog:
    return PlayerLog(
        fight=FightRef(
            report_code or f"report-{name}",
            1,
            target.encounter_id,
            "Boss",
            target.difficulty,
            300.0,
            True,
            partition=target.partition,
        ),
        build=PlayerBuild(
            character_name=name,
            server="Azralon",
            class_name=target.spec.class_name,
            spec_name=target.spec.spec_name,
            role="dps",
            item_level=289.0,
            talent_hash=None,
            tier_pieces=None,
            setup=SetupProfile(talents=(TalentNode(1, 1),)),
        ),
        dps=100_000.0,
        percentile=percentile,
        cast_timeline={},
    )


def _observations(n: int = 10, *, target: EncounterBenchmarkTarget = TARGET) -> list[PlayerLog]:
    return [_log(f"p{i}", 95.0 + i * 0.3, target=target) for i in range(n)]


def _benchmark(
    observations: list[PlayerLog] | None = None,
    *,
    target: EncounterBenchmarkTarget = TARGET,
    policy: BenchmarkPolicy | None = None,
) -> tuple[EncounterBenchmark, BenchmarkPolicy, list[PlayerLog]]:
    obs = observations if observations is not None else _observations(target=target)
    pol = policy or BenchmarkPolicy.default()
    return build_encounter_benchmark(obs, target=target, policy=pol), pol, obs


# -- 1-7: round-trip completo ---------------------------------------------------


def test_write_read_round_trip_is_exact(tmp_path: Path) -> None:
    bstore, _ = _store(tmp_path)
    benchmark, policy, obs = _benchmark()
    bstore.write_benchmark(benchmark, policy=policy, observations=obs)

    restored = bstore.read_benchmark(TARGET.benchmark_id)

    assert restored == benchmark  # 1, 3 (bands), 4 (prevalence), 5 (stats), 6 (coverage)


def test_benchmark_id_round_trips(tmp_path: Path) -> None:
    bstore, _ = _store(tmp_path)
    benchmark, policy, obs = _benchmark()
    bstore.write_benchmark(benchmark, policy=policy, observations=obs)
    restored = bstore.read_benchmark(TARGET.benchmark_id)
    assert restored is not None
    assert restored.target.benchmark_id == benchmark.target.benchmark_id


def test_insufficient_status_is_preserved(tmp_path: Path) -> None:
    bstore, _ = _store(tmp_path)
    policy = BenchmarkPolicy(min_sample_size=100)
    benchmark, pol, obs = _benchmark(_observations(3), policy=policy)
    assert benchmark.bands["p95-99"].status == "insufficient"

    bstore.write_benchmark(benchmark, policy=pol, observations=obs)
    restored = bstore.read_benchmark(TARGET.benchmark_id)

    assert restored is not None
    assert restored.bands["p95-99"].status == "insufficient"


def test_missing_lookup_returns_none(tmp_path: Path) -> None:
    bstore, _ = _store(tmp_path)
    assert bstore.read_benchmark(TARGET.benchmark_id) is None  # 23


# -- 8/9: fingerprint determinístico ---------------------------------------------


def test_population_fingerprint_is_order_independent() -> None:
    obs = _observations()
    shuffled = list(obs)
    random.Random(7).shuffle(shuffled)
    assert population_fingerprint(obs) == population_fingerprint(shuffled)


def test_population_fingerprint_distinguishes_different_populations() -> None:
    a = _observations(10)
    b = _observations(11)
    assert population_fingerprint(a) != population_fingerprint(b)


def test_population_fingerprint_never_uses_python_hash() -> None:
    """Checa o BYTECODE, não a string-fonte — o docstring da própria função
    menciona `hash()` em prosa, o que faria um grep textual falso-positivar.
    `co_names` lista os nomes globais que o código de fato referencia.
    """
    from botgitgud.analysis.benchmark_store_models import population_fingerprint

    assert "hash" not in population_fingerprint.__code__.co_names


def test_policy_fingerprint_is_deterministic_and_order_independent() -> None:
    forward = BenchmarkPolicy(
        bands=(PercentileBand("a", 50.0, 75.0), PercentileBand("b", 75.0, 95.0))
    )
    backward = BenchmarkPolicy(
        bands=(PercentileBand("b", 75.0, 95.0), PercentileBand("a", 50.0, 75.0))
    )
    assert policy_fingerprint(forward) == policy_fingerprint(backward)


def test_policy_fingerprint_distinguishes_different_bodies_same_version() -> None:
    a = BenchmarkPolicy(policy_version="v1", min_sample_size=8)
    b = BenchmarkPolicy(policy_version="v1", min_sample_size=50)
    assert a.policy_version == b.policy_version
    assert policy_fingerprint(a) != policy_fingerprint(b)


# -- 10/11/12: upsert / coexistência ----------------------------------------------


def test_same_identity_upserts_and_preserves_created_at(tmp_path: Path) -> None:
    bstore, _ = _store(tmp_path)
    benchmark, policy, obs = _benchmark(_observations(10))
    bstore.write_benchmark(benchmark, policy=policy, observations=obs)
    first = bstore.list_benchmarks()[0]

    benchmark2, policy2, obs2 = _benchmark(_observations(20))
    bstore.write_benchmark(benchmark2, policy=policy2, observations=obs2)
    rows = bstore.list_benchmarks()

    assert len(rows) == 1  # mesma identidade -> mesma linha
    assert rows[0]["created_at"] == first["created_at"]  # não reescrito
    assert rows[0]["source_observation_count"] == 20  # conteúdo atualizado

    restored = bstore.read_benchmark(TARGET.benchmark_id)
    assert restored == benchmark2


def test_different_policy_version_coexists(tmp_path: Path) -> None:
    bstore, _ = _store(tmp_path)
    target_v1 = EncounterBenchmarkTarget(
        spec=TARGET.spec,
        encounter_id=TARGET.encounter_id,
        difficulty=TARGET.difficulty,
        partition=TARGET.partition,
        benchmark_policy_version="v1",
    )
    target_v2 = EncounterBenchmarkTarget(
        spec=TARGET.spec,
        encounter_id=TARGET.encounter_id,
        difficulty=TARGET.difficulty,
        partition=TARGET.partition,
        benchmark_policy_version="v2",
    )
    b1, p1, o1 = _benchmark(
        _observations(target=target_v1),
        target=target_v1,
        policy=BenchmarkPolicy(policy_version="v1"),
    )
    b2, p2, o2 = _benchmark(
        _observations(target=target_v2),
        target=target_v2,
        policy=BenchmarkPolicy(policy_version="v2"),
    )

    bstore.write_benchmark(b1, policy=p1, observations=o1)
    bstore.write_benchmark(b2, policy=p2, observations=o2)

    assert bstore.read_benchmark(target_v1.benchmark_id) is not None
    assert bstore.read_benchmark(target_v2.benchmark_id) is not None
    assert len(bstore.list_benchmarks()) == 2  # nenhum sobrescreveu o outro


def test_different_partition_coexists(tmp_path: Path) -> None:
    bstore, _ = _store(tmp_path)
    target_p3 = TARGET
    target_p4 = EncounterBenchmarkTarget(
        spec=TARGET.spec,
        encounter_id=TARGET.encounter_id,
        difficulty=TARGET.difficulty,
        partition=4,
    )
    b1, p1, o1 = _benchmark(target=target_p3)
    b2, p2, o2 = _benchmark(_observations(target=target_p4), target=target_p4)

    bstore.write_benchmark(b1, policy=p1, observations=o1)
    bstore.write_benchmark(b2, policy=p2, observations=o2)

    assert len(bstore.list_benchmarks()) == 2


def test_write_rejects_a_policy_that_does_not_match_the_benchmark(tmp_path: Path) -> None:
    bstore, _ = _store(tmp_path)
    benchmark, _policy, obs = _benchmark(policy=BenchmarkPolicy(policy_version="v1"))
    mismatched_policy = BenchmarkPolicy(policy_version="v2")
    with pytest.raises(ValueError, match="policy_version"):
        bstore.write_benchmark(benchmark, policy=mismatched_policy, observations=obs)


# -- 13-21: freshness --------------------------------------------------------------


def test_fresh_benchmark(tmp_path: Path) -> None:
    bstore, _ = _store(tmp_path)
    benchmark, policy, obs = _benchmark()
    bstore.write_benchmark(benchmark, policy=policy, observations=obs)

    freshness = bstore.evaluate_benchmark_freshness(
        target=TARGET, policy=policy, current_population_size=len(obs)
    )
    assert freshness.status == "fresh"
    assert freshness.reasons == ()


def test_partition_change_marks_stale(tmp_path: Path) -> None:
    bstore, _ = _store(tmp_path)
    benchmark, policy, obs = _benchmark(target=TARGET)
    bstore.write_benchmark(benchmark, policy=policy, observations=obs)

    new_partition_target = EncounterBenchmarkTarget(
        spec=TARGET.spec,
        encounter_id=TARGET.encounter_id,
        difficulty=TARGET.difficulty,
        partition=4,
    )
    freshness = bstore.evaluate_benchmark_freshness(
        target=new_partition_target, policy=policy, current_population_size=len(obs)
    )
    assert freshness.status == "stale"
    assert "partition_changed" in freshness.reasons
    assert freshness.benchmark_id == TARGET.benchmark_id  # ainda aponta pro que foi achado


def test_policy_version_change_marks_stale(tmp_path: Path) -> None:
    bstore, _ = _store(tmp_path)
    benchmark, policy, obs = _benchmark(policy=BenchmarkPolicy(policy_version="v1"))
    bstore.write_benchmark(benchmark, policy=policy, observations=obs)

    new_policy_target = EncounterBenchmarkTarget(
        spec=TARGET.spec,
        encounter_id=TARGET.encounter_id,
        difficulty=TARGET.difficulty,
        partition=TARGET.partition,
        benchmark_policy_version="v2",
    )
    freshness = bstore.evaluate_benchmark_freshness(
        target=new_policy_target,
        policy=BenchmarkPolicy(policy_version="v2"),
        current_population_size=len(obs),
    )
    assert freshness.status == "stale"
    assert "policy_changed" in freshness.reasons


def test_same_version_string_different_policy_body_marks_stale(tmp_path: Path) -> None:
    """policy_fingerprint pega drift que policy_version sozinha não pegaria."""
    bstore, _ = _store(tmp_path)
    original_policy = BenchmarkPolicy(policy_version="v1", min_sample_size=8)
    benchmark, _pol, obs = _benchmark(policy=original_policy)
    bstore.write_benchmark(benchmark, policy=original_policy, observations=obs)

    drifted_policy = BenchmarkPolicy(policy_version="v1", min_sample_size=50)
    freshness = bstore.evaluate_benchmark_freshness(
        target=TARGET, policy=drifted_policy, current_population_size=len(obs)
    )
    assert "policy_changed" in freshness.reasons


@pytest.mark.parametrize(
    # baseline fixo em 100 e `current_n` escolhido a dedo (nunca via
    # `round(baseline * (1 + ratio))`, que colide arredondamento de float
    # com o próprio threshold) — 130 é EXATAMENTE 30% acima de 100; 131 é
    # a menor contagem inteira mensuravelmente acima da fronteira.
    ("current_n", "expect_stale"),
    [(100, False), (130, False), (131, True), (150, True)],
)
def test_population_growth_boundaries(tmp_path: Path, current_n: int, expect_stale: bool) -> None:
    bstore, _ = _store(tmp_path)
    baseline_n = 100
    benchmark, policy, obs = _benchmark(_observations(baseline_n))
    bstore.write_benchmark(benchmark, policy=policy, observations=obs)

    freshness = bstore.evaluate_benchmark_freshness(
        target=TARGET, policy=policy, current_population_size=current_n
    )
    if expect_stale:
        assert "population_growth" in freshness.reasons
        assert freshness.status == "stale"
    else:
        assert "population_growth" not in freshness.reasons


def test_population_growth_ratio_helper_boundaries() -> None:
    assert population_growth_ratio(100, 100) == 0.0
    assert population_growth_ratio(100, 130) == pytest.approx(0.30)
    assert population_growth_ratio(0, 0) == 0.0
    assert population_growth_ratio(0, 5) == float("inf")


def test_ttl_under_seven_days_is_fresh(tmp_path: Path) -> None:
    bstore, _ = _store(tmp_path)
    benchmark, policy, obs = _benchmark()
    bstore.write_benchmark(benchmark, policy=policy, observations=obs)

    freshness = bstore.evaluate_benchmark_freshness(
        target=TARGET,
        policy=policy,
        current_population_size=len(obs),
        now=now_utc_naive() + timedelta(days=6.9),
    )
    assert "max_age_exceeded" not in freshness.reasons


def test_ttl_over_seven_days_is_stale(tmp_path: Path) -> None:
    bstore, _ = _store(tmp_path)
    benchmark, policy, obs = _benchmark()
    bstore.write_benchmark(benchmark, policy=policy, observations=obs)

    freshness = bstore.evaluate_benchmark_freshness(
        target=TARGET,
        policy=policy,
        current_population_size=len(obs),
        now=now_utc_naive() + timedelta(days=7.1),
    )
    assert "max_age_exceeded" in freshness.reasons
    assert freshness.status == "stale"


def test_ttl_at_exactly_seven_days_is_fresh(tmp_path: Path) -> None:
    """Fronteira sem ambiguidade: exatamente no TTL ainda conta como fresco.

    Usa o `updated_at` REALMENTE persistido (não um novo `now_utc_naive()`
    recalculado aqui) — os dois momentos diferem por microssegundos de
    tempo real de execução, o suficiente para empurrar `age_days` para
    7.0000003, um falso "acima do TTL" que não tem nada a ver com a regra
    sendo testada.
    """
    bstore, _ = _store(tmp_path)
    benchmark, policy, obs = _benchmark()
    bstore.write_benchmark(benchmark, policy=policy, observations=obs)
    persisted_updated_at = bstore.list_benchmarks()[0]["updated_at"]
    assert isinstance(persisted_updated_at, datetime)

    freshness = bstore.evaluate_benchmark_freshness(
        target=TARGET,
        policy=policy,
        current_population_size=len(obs),
        now=persisted_updated_at + timedelta(days=7.0),
    )
    assert "max_age_exceeded" not in freshness.reasons


def test_missing_benchmark_freshness_is_explicit(tmp_path: Path) -> None:
    bstore, _ = _store(tmp_path)
    freshness = bstore.evaluate_benchmark_freshness(
        target=TARGET, policy=BenchmarkPolicy.default(), current_population_size=10
    )
    assert freshness.status == "stale"
    assert freshness.reasons == ("missing",)
    assert freshness.benchmark_id is None


def test_max_data_age_days_is_never_used_as_ttl(tmp_path: Path) -> None:
    """21: BenchmarkPolicy.max_data_age_days (EB.1, elegibilidade de DADO)
    nunca deve influenciar staleness de CACHE — só `ttl_days` de
    `BenchmarkFreshnessPolicy` (dedicado, separado) importa.
    """
    bstore, _ = _store(tmp_path)
    policy = BenchmarkPolicy(
        max_data_age_days=99999.0
    )  # gigante, se fosse usado como TTL nunca seria stale
    benchmark, pol, obs = _benchmark(policy=policy)
    bstore.write_benchmark(benchmark, policy=pol, observations=obs)

    freshness = bstore.evaluate_benchmark_freshness(
        target=TARGET,
        policy=policy,
        current_population_size=len(obs),
        freshness_policy=BenchmarkFreshnessPolicy(ttl_days=1.0),
        now=now_utc_naive() + timedelta(days=2.0),
    )
    assert "max_age_exceeded" in freshness.reasons  # o TTL dedicado (1 dia) venceu


def test_evaluate_benchmark_freshness_source_never_reads_max_data_age_days() -> None:
    import inspect

    from botgitgud.analysis.benchmark_store import BenchmarkStore as _BS

    source = inspect.getsource(_BS.evaluate_benchmark_freshness)
    assert "max_data_age_days" not in source


# -- 22: corrupção ------------------------------------------------------------------


def test_corrupt_payload_fails_closed_on_read(tmp_path: Path) -> None:
    bstore, store = _store(tmp_path)
    benchmark, policy, obs = _benchmark()
    bstore.write_benchmark(benchmark, policy=policy, observations=obs)

    store.execute(
        "UPDATE encounter_benchmarks SET benchmark_payload = ? WHERE benchmark_id = ?",
        ["not-json-at-all{{{", TARGET.benchmark_id],
    )

    with pytest.raises(EncounterBenchmarkCorruptPayloadError):
        bstore.read_benchmark(TARGET.benchmark_id)


def test_corrupt_payload_never_returns_a_partial_benchmark(tmp_path: Path) -> None:
    bstore, store = _store(tmp_path)
    benchmark, policy, obs = _benchmark()
    bstore.write_benchmark(benchmark, policy=policy, observations=obs)

    store.execute(
        "UPDATE encounter_benchmarks SET benchmark_payload = ? WHERE benchmark_id = ?",
        ['{"target": {}}', TARGET.benchmark_id],  # JSON válido, mas incompleto
    )

    with pytest.raises(EncounterBenchmarkCorruptPayloadError):
        bstore.read_benchmark(TARGET.benchmark_id)


# -- 24: cobertura parcial é persistível ----------------------------------------------


def test_partial_setup_coverage_is_persistible(tmp_path: Path) -> None:
    bstore, _ = _store(tmp_path)
    with_setup = _observations(6)
    without_setup = [
        PlayerLog(
            fight=FightRef(
                f"report-nosetup{i}",
                1,
                TARGET.encounter_id,
                "Boss",
                TARGET.difficulty,
                300.0,
                True,
                partition=TARGET.partition,
            ),
            build=PlayerBuild(
                f"nosetup{i}",
                "Azralon",
                TARGET.spec.class_name,
                TARGET.spec.spec_name,
                "dps",
                289.0,
                None,
                None,
                setup=None,
            ),
            dps=100_000.0,
            percentile=96.0 + i * 0.1,
            cast_timeline={},
        )
        for i in range(4)
    ]
    obs = with_setup + without_setup
    benchmark = build_encounter_benchmark(obs, target=TARGET, policy=BenchmarkPolicy.default())
    assert benchmark.missing_setup_count == 4

    bstore.write_benchmark(benchmark, policy=BenchmarkPolicy.default(), observations=obs)
    restored = bstore.read_benchmark(TARGET.benchmark_id)

    assert restored is not None
    assert restored.missing_setup_count == 4
    row = bstore.list_benchmarks()[0]
    assert row["setup_missing_count"] == 4
    assert row["setup_available_count"] == 6


# -- 25/26/27: atomicidade, migração idempotente, reopen ---------------------------


def test_migration_is_idempotent(tmp_path: Path) -> None:
    store = Store(tmp_path)
    BenchmarkStore(store)  # primeira "migração"
    BenchmarkStore(store)  # segunda, sobre o mesmo warehouse — não deve levantar
    store.close()


def test_benchmark_survives_store_reopen(tmp_path: Path) -> None:
    store = Store(tmp_path)
    bstore = BenchmarkStore(store)
    benchmark, policy, obs = _benchmark()
    bstore.write_benchmark(benchmark, policy=policy, observations=obs)
    store.close()

    store2 = Store(tmp_path)
    bstore2 = BenchmarkStore(store2)
    restored = bstore2.read_benchmark(TARGET.benchmark_id)
    store2.close()

    assert restored == benchmark


# -- 28/29: zero WCL / Discord --------------------------------------------------------


def test_no_io_or_network_imports_in_the_store_models_module() -> None:
    import inspect

    from botgitgud.analysis import benchmark_store_models as module

    source = inspect.getsource(module)
    for forbidden in ("import aiohttp", "import discord", "requests.", "duckdb"):
        assert forbidden not in source
