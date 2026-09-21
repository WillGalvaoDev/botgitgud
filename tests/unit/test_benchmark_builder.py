"""EB.4 — construção incremental/resumível do Encounter Benchmark.

Zero rede real: `FakeWclBackend` (tests/fixtures/fake_wcl_backend.py)
produz exatamente o formato de resposta que `ingest/fight_rankings.py` e
`ingest/benchmark_fetch.py` esperam, verificado contra um cassete real
antes deste arquivo ser escrito.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fake_wcl_backend import FakeBudgetClient, FakePlayer, FakeWclBackend

from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget, PercentileBand
from botgitgud.analysis.benchmark_aggregate import build_encounter_benchmark
from botgitgud.analysis.benchmark_build_budget import protected_floor
from botgitgud.analysis.benchmark_build_progress import BenchmarkBuildProgressStore
from botgitgud.analysis.benchmark_builder import (
    BenchmarkBuildState,
    LocalBenchmarkRebuildError,
    advance_benchmark_build,
    build_benchmark_until_budget,
    rebuild_benchmark_from_local_progress,
)
from botgitgud.analysis.benchmark_store import BenchmarkStore
from botgitgud.config import Settings
from botgitgud.domain.models import (
    FightRef,
    PlayerBuild,
    PlayerLog,
    RankingCandidate,
    SetupProfile,
    TalentNode,
)
from botgitgud.domain.specs import SpecId
from botgitgud.ingest.store import Store

TARGET = EncounterBenchmarkTarget(
    spec=SpecId("Warlock", "Demonology"), encounter_id=3179, difficulty=5, partition=3
)
BAD_BUILD = ((100, 1),)
GOOD_BUILD = ((200, 1),)


def _settings(**overrides: object) -> Settings:
    from pydantic import SecretStr

    defaults: dict[str, object] = {
        "discord_token": SecretStr("x"),
        "wcl_client_id": SecretStr("x"),
        "wcl_client_secret": SecretStr("x"),
        "blizzard_client_id": SecretStr("x"),
        "blizzard_client_secret": SecretStr("x"),
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _env(tmp_path: Path) -> tuple[Store, BenchmarkStore, BenchmarkBuildProgressStore]:
    store = Store(tmp_path)
    return store, BenchmarkStore(store), BenchmarkBuildProgressStore(store)


def _candidates_for(backend: FakeWclBackend) -> list[RankingCandidate]:
    out = []
    for (report_code, fight_id), players in backend.fights.items():
        duration = backend.fight_duration_s.get((report_code, fight_id), 300.0)
        out.extend(RankingCandidate(report_code, fight_id, p.name, duration) for p in players)
    return out


def _ample_client() -> FakeBudgetClient:
    return FakeBudgetClient(points_remaining=1_000_000.0)


def test_local_cross_version_rebuild_preserves_population_and_v1(tmp_path: Path) -> None:
    _store, bstore, pstore = _env(tmp_path)
    source = EncounterBenchmarkTarget(
        spec=TARGET.spec,
        encounter_id=TARGET.encounter_id,
        difficulty=TARGET.difficulty,
        partition=TARGET.partition,
        benchmark_policy_version="v1",
    )
    old_policy = BenchmarkPolicy(
        policy_version="v1",
        bands=(
            PercentileBand("p50-75", 50.0, 75.0),
            PercentileBand("p75-95", 75.0, 95.0),
            PercentileBand("p95-99", 95.0, 99.0),
        ),
    )
    setup = SetupProfile(talents=(TalentNode(100, 1),), gear=(), stats={})
    observation = PlayerLog(
        fight=FightRef("local", 1, 3179, "Boss", 5, 300.0, True, partition=3),
        build=PlayerBuild(
            character_name="Player",
            server="Realm",
            class_name="Warlock",
            spec_name="Demonology",
            role="dps",
            item_level=300.0,
            talent_hash=None,
            tier_pieces=None,
            setup=setup,
        ),
        dps=None,
        percentile=99.0,
        cast_timeline={},
    )
    candidate = RankingCandidate("local", 1, "Player", 300.0)
    pstore.register_candidates(source.benchmark_id, [candidate])
    pstore.mark_fetched(
        source.benchmark_id,
        "local",
        1,
        "Player",
        band="outside_policy_bands",
        rank_percent=99.0,
        duration_s=300.0,
        item_level=300.0,
        class_name="Warlock",
        spec_name="Demonology",
        server="Realm",
        partition=3,
        setup=setup,
    )
    old_benchmark = build_encounter_benchmark([observation], target=source, policy=old_policy)
    bstore.write_benchmark(old_benchmark, policy=old_policy, observations=[observation])

    result = rebuild_benchmark_from_local_progress(
        progress_store=pstore,
        benchmark_store=bstore,
        source_target=source,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
    )

    assert result.observations_reused == 1
    assert result.before_by_band == {"outside_policy_bands": 1}
    assert result.after_by_band == {"p99-100": 1}
    assert bstore.read_benchmark(source.benchmark_id) == old_benchmark
    rebuilt = bstore.read_benchmark(TARGET.benchmark_id)
    assert rebuilt is not None
    assert rebuilt.bands["p99-100"].sample_size == 1


def test_local_rebuild_has_no_network_inputs_and_rejects_missing_source(tmp_path: Path) -> None:
    parameters = inspect.signature(rebuild_benchmark_from_local_progress).parameters
    assert "query_fn" not in parameters
    assert "client" not in parameters
    _, bstore, pstore = _env(tmp_path)
    source = EncounterBenchmarkTarget(
        spec=TARGET.spec,
        encounter_id=3179,
        difficulty=5,
        partition=3,
        benchmark_policy_version="v1",
    )
    with pytest.raises(LocalBenchmarkRebuildError, match="does not exist"):
        rebuild_benchmark_from_local_progress(
            progress_store=pstore,
            benchmark_store=bstore,
            source_target=source,
            target=TARGET,
            policy=BenchmarkPolicy.default(),
        )


# -- 1-4: cache local (Store.read_log) ---------------------------------------


def test_build_100pct_local_cache_makes_zero_network_calls(tmp_path: Path) -> None:
    store, bstore, pstore = _env(tmp_path)
    log = PlayerLog(
        fight=FightRef("repA", 1, 3179, "Boss", 5, 300.0, True, partition=3),
        build=PlayerBuild(
            "Alpha",
            "Azralon",
            "Warlock",
            "Demonology",
            "dps",
            289.0,
            None,
            None,
            setup=SetupProfile(talents=(TalentNode(1, 1),)),
        ),
        dps=100_000.0,
        percentile=97.0,
        cast_timeline={},
    )
    store.write_log(log)

    def fail_on_any_call(*_a: object, **_kw: object) -> dict[str, object]:
        raise AssertionError("network should never be touched")

    result = advance_benchmark_build(
        query_fn=fail_on_any_call,
        client=_ample_client(),
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=_settings(),
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=[RankingCandidate("repA", 1, "Alpha", 300.0)],
    )
    assert result.state is BenchmarkBuildState.READY
    assert result.cache_hits == 1
    assert result.newly_fetched == 0


def test_partial_local_cache_only_fetches_the_rest(tmp_path: Path) -> None:
    store, bstore, pstore = _env(tmp_path)
    log = PlayerLog(
        fight=FightRef("repA", 1, 3179, "Boss", 5, 300.0, True, partition=3),
        build=PlayerBuild(
            "Alpha",
            "Azralon",
            "Warlock",
            "Demonology",
            "dps",
            289.0,
            None,
            None,
            setup=SetupProfile(talents=(TalentNode(1, 1),)),
        ),
        dps=100_000.0,
        percentile=97.0,
        cast_timeline={},
    )
    store.write_log(log)  # Alpha só

    backend = FakeWclBackend()
    backend.add_fight("repB", 1, (FakePlayer("Beta", rank_percent=60.0),))
    candidates = [
        RankingCandidate("repA", 1, "Alpha", 300.0),
        RankingCandidate("repB", 1, "Beta", 300.0),
    ]
    result = advance_benchmark_build(
        query_fn=backend.query_fn,
        client=_ample_client(),
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=_settings(),
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=candidates,
    )
    assert result.state is BenchmarkBuildState.READY
    assert result.cache_hits == 1  # Alpha
    assert result.newly_fetched == 1  # Beta
    assert {k[1] for k in backend.query_log} == {"repB"}  # nunca tocou repA


def test_setup_none_old_log_requires_setup_only_fetch(tmp_path: Path) -> None:
    """3: um PlayerLog pré-EB.0 (`setup=None`) NÃO conta como cache hit."""
    store, bstore, pstore = _env(tmp_path)
    old_log = PlayerLog(
        fight=FightRef("repA", 1, 3179, "Boss", 5, 300.0, True, partition=3),
        build=PlayerBuild(
            "Alpha", "Azralon", "Warlock", "Demonology", "dps", 289.0, None, None, setup=None
        ),
        dps=100_000.0,
        percentile=97.0,
        cast_timeline={},
    )
    store.write_log(old_log)

    backend = FakeWclBackend()
    backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
    result = advance_benchmark_build(
        query_fn=backend.query_fn,
        client=_ample_client(),
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=_settings(),
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=[RankingCandidate("repA", 1, "Alpha", 300.0)],
    )
    assert result.cache_hits == 0
    assert result.newly_fetched == 1
    assert len(backend.query_log) == 2  # rankings + setup-only


def test_existing_setup_is_never_refetched(tmp_path: Path) -> None:
    """4: mesmo cenário de #1, mas afirmando explicitamente que a rede
    nunca é sequer construída/chamada uma segunda vez para o mesmo build.
    """
    store, bstore, pstore = _env(tmp_path)
    log = PlayerLog(
        fight=FightRef("repA", 1, 3179, "Boss", 5, 300.0, True, partition=3),
        build=PlayerBuild(
            "Alpha",
            "Azralon",
            "Warlock",
            "Demonology",
            "dps",
            289.0,
            None,
            None,
            setup=SetupProfile(talents=(TalentNode(1, 1),)),
        ),
        dps=100_000.0,
        percentile=97.0,
        cast_timeline={},
    )
    store.write_log(log)
    backend = FakeWclBackend()  # nenhum fight registrado -> qualquer query levanta KeyError/assert

    for _ in range(2):
        result = advance_benchmark_build(
            query_fn=backend.query_fn,
            client=_ample_client(),
            log_store=store,
            progress_store=pstore,
            benchmark_store=bstore,
            settings=_settings(),
            target=TARGET,
            policy=BenchmarkPolicy.default(),
            candidates=[RankingCandidate("repA", 1, "Alpha", 300.0)],
        )
        assert result.state is BenchmarkBuildState.READY
    assert backend.query_log == []


# -- 5/6: compartilhamento fight-wide -----------------------------------------


def test_five_players_same_fight_costs_exactly_one_setup_query(tmp_path: Path) -> None:
    store, bstore, pstore = _env(tmp_path)
    backend = FakeWclBackend()
    players = tuple(FakePlayer(f"P{i}", rank_percent=60.0 + i) for i in range(5))
    backend.add_fight("repA", 1, players)
    candidates = [RankingCandidate("repA", 1, p.name, 300.0) for p in players]

    result = advance_benchmark_build(
        query_fn=backend.query_fn,
        client=_ample_client(),
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=_settings(),
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=candidates,
    )
    assert result.newly_fetched == 5
    setup_calls = [k for k in backend.query_log if k[0] == "fetch_player_setup_only"]
    rankings_calls = [k for k in backend.query_log if k[0] == "fetch_report_rankings"]
    assert len(setup_calls) == 1
    assert len(rankings_calls) == 1


def test_multiple_fights_cost_exactly_one_query_pair_each(tmp_path: Path) -> None:
    store, bstore, pstore = _env(tmp_path)
    backend = FakeWclBackend()
    for i in range(4):
        backend.add_fight(f"rep{i}", 1, (FakePlayer(f"P{i}", rank_percent=60.0),))
    candidates = _candidates_for(backend)

    result = advance_benchmark_build(
        query_fn=backend.query_fn,
        client=_ample_client(),
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=_settings(),
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=candidates,
    )
    assert result.newly_fetched == 4
    assert len(backend.query_log) == 8  # 2 por fight, 4 fights


# -- 7: dedup não duplica trabalho de rede -------------------------------------


def test_exact_duplicate_candidate_does_not_cost_extra_queries(tmp_path: Path) -> None:
    store, bstore, pstore = _env(tmp_path)
    backend = FakeWclBackend()
    backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
    duplicated = [
        RankingCandidate("repA", 1, "Alpha", 300.0),
        RankingCandidate("repA", 1, "Alpha", 300.0),  # duplicata exata
    ]
    advance_benchmark_build(
        query_fn=backend.query_fn,
        client=_ample_client(),
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=_settings(),
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=duplicated,
    )
    assert len(backend.query_log) == 2  # nunca 4 — mesma identidade de linha (PK) colapsa


# -- 8/9/10: bandas, progresso por banda, insufficient -------------------------


def test_completed_by_band_reflects_actual_rank_percent(tmp_path: Path) -> None:
    store, bstore, pstore = _env(tmp_path)
    backend = FakeWclBackend()
    backend.add_fight(
        "repA",
        1,
        (
            FakePlayer("Top", rank_percent=97.0),
            FakePlayer("Mid", rank_percent=80.0),
            FakePlayer("Low", rank_percent=60.0),
        ),
    )
    candidates = _candidates_for(backend)
    result = advance_benchmark_build(
        query_fn=backend.query_fn,
        client=_ample_client(),
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=_settings(),
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=candidates,
    )
    assert result.completed_by_band == {"p95-99": 1, "p75-95": 1, "p50-75": 1}
    assert result.planned_by_band == {"p95-99": 1, "p75-95": 1, "p50-75": 1}
    assert result.remaining_by_band == {}


def test_insufficient_band_is_preserved_in_the_final_benchmark(tmp_path: Path) -> None:
    store, bstore, pstore = _env(tmp_path)
    backend = FakeWclBackend()
    backend.add_fight("repA", 1, (FakePlayer("Solo", rank_percent=97.0),))
    candidates = _candidates_for(backend)
    policy = BenchmarkPolicy(min_sample_size=100)

    result = advance_benchmark_build(
        query_fn=backend.query_fn,
        client=_ample_client(),
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=_settings(),
        target=TARGET,
        policy=policy,
        candidates=candidates,
    )
    assert result.state is BenchmarkBuildState.READY
    benchmark = bstore.read_benchmark(TARGET.benchmark_id)
    assert benchmark is not None
    assert benchmark.bands["p95-99"].status == "insufficient"


# -- 11/12/13: janela parcial, resume, zero refetch ----------------------------


def test_first_partial_window_defers_with_progress_preserved(tmp_path: Path) -> None:
    store, bstore, pstore = _env(tmp_path)
    backend = FakeWclBackend()
    for i in range(3):
        backend.add_fight(f"rep{i}", 1, (FakePlayer(f"P{i}", rank_percent=60.0 + i),))
    candidates = _candidates_for(backend)
    settings = _settings()
    tiny = FakeBudgetClient(
        points_remaining=protected_floor(settings) + settings.benchmark_build_safety_margin
    )

    result = advance_benchmark_build(
        query_fn=backend.query_fn,
        client=tiny,
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=settings,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=candidates,
    )
    assert result.state is BenchmarkBuildState.DEFERRED_BUDGET
    assert result.completed < result.planned
    assert (
        bstore.read_benchmark(TARGET.benchmark_id) is None
    )  # 25: parcial nunca aparece como final


def test_second_window_resumes_and_completes(tmp_path: Path) -> None:
    store, bstore, pstore = _env(tmp_path)
    backend = FakeWclBackend()
    for i in range(3):
        backend.add_fight(f"rep{i}", 1, (FakePlayer(f"P{i}", rank_percent=60.0 + i),))
    candidates = _candidates_for(backend)
    settings = _settings()
    tiny = FakeBudgetClient(
        points_remaining=protected_floor(settings) + settings.benchmark_build_safety_margin
    )

    advance_benchmark_build(
        query_fn=backend.query_fn,
        client=tiny,
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=settings,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=candidates,
    )
    tiny.points_remaining = 1_000_000.0  # orçamento renovado
    result = advance_benchmark_build(
        query_fn=backend.query_fn,
        client=tiny,
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=settings,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=candidates,
    )
    assert result.state is BenchmarkBuildState.READY
    assert result.completed == 3


def test_zero_refetch_across_multiple_resumed_windows(tmp_path: Path) -> None:
    store, bstore, pstore = _env(tmp_path)
    backend = FakeWclBackend()
    for i in range(3):
        backend.add_fight(f"rep{i}", 1, (FakePlayer(f"P{i}", rank_percent=60.0 + i),))
    candidates = _candidates_for(backend)
    settings = _settings()
    tiny = FakeBudgetClient(
        points_remaining=protected_floor(settings) + settings.benchmark_build_safety_margin
    )

    advance_benchmark_build(
        query_fn=backend.query_fn,
        client=tiny,
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=settings,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=candidates,
    )
    fights_attempted_window1 = set(backend.query_log)
    tiny.points_remaining = 1_000_000.0
    advance_benchmark_build(
        query_fn=backend.query_fn,
        client=tiny,
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=settings,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=candidates,
    )
    # cada (op, report, fight) só aparece UMA vez no log inteiro (2 janelas somadas)
    assert len(backend.query_log) == len(set(backend.query_log))
    assert set(backend.query_log) >= fights_attempted_window1


# -- 14-17: budget -----------------------------------------------------------


def test_low_budget_defers(tmp_path: Path) -> None:
    store, bstore, pstore = _env(tmp_path)
    backend = FakeWclBackend()
    backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
    settings = _settings()
    starved = FakeBudgetClient(points_remaining=protected_floor(settings))  # nem a margem cabe

    result = advance_benchmark_build(
        query_fn=backend.query_fn,
        client=starved,
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=settings,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
    )
    assert result.state is BenchmarkBuildState.DEFERRED_BUDGET
    assert backend.query_log == []  # nunca gastou nada furando o piso


def test_renewed_budget_continues(tmp_path: Path) -> None:
    store, bstore, pstore = _env(tmp_path)
    backend = FakeWclBackend()
    backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
    settings = _settings()
    client = FakeBudgetClient(points_remaining=protected_floor(settings))
    advance_benchmark_build(
        query_fn=backend.query_fn,
        client=client,
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=settings,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
    )
    client.points_remaining = 1_000_000.0
    result = advance_benchmark_build(
        query_fn=backend.query_fn,
        client=client,
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=settings,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
    )
    assert result.state is BenchmarkBuildState.READY


def test_runtime_budget_is_reevaluated_every_batch(tmp_path: Path) -> None:
    """16: orçamento é reconferido a cada lote — não só uma vez no início.
    `refresh_budget()` é chamado mais de uma vez para múltiplos fights.
    """
    store, bstore, pstore = _env(tmp_path)
    backend = FakeWclBackend()
    settings = _settings(benchmark_build_chunk_fights=1)
    for i in range(3):
        backend.add_fight(f"rep{i}", 1, (FakePlayer(f"P{i}", rank_percent=60.0 + i),))
    client = FakeBudgetClient(points_remaining=1_000_000.0)

    advance_benchmark_build(
        query_fn=backend.query_fn,
        client=client,
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=settings,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
    )
    assert client.refresh_calls >= 3  # uma reavaliação por fight (lote de 1)


def test_never_violates_the_simulated_floor(tmp_path: Path) -> None:
    store, bstore, pstore = _env(tmp_path)
    backend = FakeWclBackend()
    for i in range(10):
        backend.add_fight(f"rep{i}", 1, (FakePlayer(f"P{i}", rank_percent=60.0 + i),))
    settings = _settings()
    floor = protected_floor(settings) + settings.benchmark_build_safety_margin
    # orçamento pouco acima do piso: só deve dar pra 1-2 fights antes de parar
    client = FakeBudgetClient(points_remaining=floor + 5.0)

    advance_benchmark_build(
        query_fn=backend.query_fn,
        client=client,
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=settings,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
    )
    # o motor nunca gasta o suficiente para deixar points_remaining < piso simulado
    # (o fake client não decrementa sozinho, então isto valida a DECISÃO de
    # affordable_fights, não um efeito colateral do fake).
    from botgitgud.analysis.benchmark_build_budget import affordable_fights

    assert affordable_fights(settings, client.points_remaining, planned=10) <= 1


# -- 18/19: no_progress ---------------------------------------------------------


def test_no_progress_is_explicit_when_every_fight_fails_transiently(tmp_path: Path) -> None:
    store, bstore, pstore = _env(tmp_path)
    backend = FakeWclBackend()
    backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
    backend.failing.add(("fetch_player_setup_only", "repA", 1))

    result = advance_benchmark_build(
        query_fn=backend.query_fn,
        client=_ample_client(),
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=_settings(),
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
    )
    assert result.state is BenchmarkBuildState.NO_PROGRESS
    assert result.completed == 0


def test_no_progress_never_becomes_ready(tmp_path: Path) -> None:
    store, bstore, pstore = _env(tmp_path)
    backend = FakeWclBackend()
    backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
    backend.failing.add(("fetch_player_setup_only", "repA", 1))

    result = advance_benchmark_build(
        query_fn=backend.query_fn,
        client=_ample_client(),
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=_settings(),
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
    )
    assert result.state is not BenchmarkBuildState.READY
    assert bstore.read_benchmark(TARGET.benchmark_id) is None


def test_transient_error_retries_then_becomes_permanent_failure(tmp_path: Path) -> None:
    """20: um erro transitório não corrompe o checkpoint — o candidato
    continua `pending` (retentável) até esgotar as tentativas, nunca vira
    dado inventado.
    """
    store, bstore, pstore = _env(tmp_path)
    backend = FakeWclBackend()
    backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
    backend.failing.add(("fetch_player_setup_only", "repA", 1))

    for _ in range(3):
        advance_benchmark_build(
            query_fn=backend.query_fn,
            client=_ample_client(),
            log_store=store,
            progress_store=pstore,
            benchmark_store=bstore,
            settings=_settings(),
            target=TARGET,
            policy=BenchmarkPolicy.default(),
            candidates=_candidates_for(backend),
        )
    rows = pstore.read_progress(TARGET.benchmark_id)
    assert rows[0].status == "failed"
    assert rows[0].attempts == 3
    assert "fetch failed" in (rows[0].last_error or "")

    # agora libera — mas o candidato já é permanentemente failed, então o
    # build fica READY com uma população vazia, não trava para sempre.
    backend.failing.clear()
    result = advance_benchmark_build(
        query_fn=backend.query_fn,
        client=_ample_client(),
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=_settings(),
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
    )
    assert result.state is BenchmarkBuildState.READY


# -- 21/22: integração EB.2/EB.3 -------------------------------------------------


def test_eb2_aggregator_is_reused_not_reimplemented(tmp_path: Path) -> None:
    store, bstore, pstore = _env(tmp_path)
    backend = FakeWclBackend()
    backend.add_fight(
        "repA",
        1,
        (
            FakePlayer("A", rank_percent=97.0, talents=BAD_BUILD),
            FakePlayer("B", rank_percent=96.0, talents=GOOD_BUILD),
        ),
    )
    result = advance_benchmark_build(
        query_fn=backend.query_fn,
        client=_ample_client(),
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=_settings(),
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
    )
    assert result.state is BenchmarkBuildState.READY
    benchmark = bstore.read_benchmark(TARGET.benchmark_id)
    assert benchmark is not None
    from botgitgud.analysis.benchmark_aggregate import talent_build_key
    from botgitgud.domain.models import SetupProfile as SP
    from botgitgud.domain.models import TalentNode as TN

    bad_key = talent_build_key(SP(talents=(TN(100, 1),)))
    good_key = talent_build_key(SP(talents=(TN(200, 1),)))
    counts = {
        e.key: e.n_observed for e in benchmark.bands["p95-99"].talent_build_prevalence.entries
    }
    assert counts == {bad_key: 1, good_key: 1}


def test_final_result_written_via_benchmark_store(tmp_path: Path) -> None:
    store, bstore, pstore = _env(tmp_path)
    backend = FakeWclBackend()
    backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
    advance_benchmark_build(
        query_fn=backend.query_fn,
        client=_ample_client(),
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=_settings(),
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
    )
    listed = bstore.list_benchmarks()
    assert len(listed) == 1
    assert listed[0]["benchmark_id"] == TARGET.benchmark_id


# -- 25: partial nunca aparece em read_benchmark ----------------------------------


def test_partial_progress_never_appears_via_read_benchmark(tmp_path: Path) -> None:
    store, bstore, pstore = _env(tmp_path)
    backend = FakeWclBackend()
    for i in range(3):
        backend.add_fight(f"rep{i}", 1, (FakePlayer(f"P{i}", rank_percent=60.0 + i),))
    settings = _settings()
    tiny = FakeBudgetClient(
        points_remaining=protected_floor(settings) + settings.benchmark_build_safety_margin
    )
    result = advance_benchmark_build(
        query_fn=backend.query_fn,
        client=tiny,
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=settings,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
    )
    assert result.state is BenchmarkBuildState.DEFERRED_BUDGET
    assert bstore.read_benchmark(TARGET.benchmark_id) is None


# -- 26/27: determinismo do fingerprint final --------------------------------------


def test_final_fingerprint_is_independent_of_candidate_order(tmp_path: Path) -> None:
    def _run(order: list[RankingCandidate]) -> str:
        store, bstore, pstore = _env(tmp_path / f"run-{id(order)}")
        backend = FakeWclBackend()
        backend.add_fight(
            "repA",
            1,
            (
                FakePlayer("A", rank_percent=97.0),
                FakePlayer("B", rank_percent=80.0),
                FakePlayer("C", rank_percent=60.0),
            ),
        )
        advance_benchmark_build(
            query_fn=backend.query_fn,
            client=_ample_client(),
            log_store=store,
            progress_store=pstore,
            benchmark_store=bstore,
            settings=_settings(),
            target=TARGET,
            policy=BenchmarkPolicy.default(),
            candidates=order,
        )
        row = bstore.list_benchmarks()[0]
        store.close()
        fingerprint = row["population_fingerprint"]
        assert isinstance(fingerprint, str)
        return fingerprint

    backend = FakeWclBackend()
    backend.add_fight(
        "repA",
        1,
        (
            FakePlayer("A", rank_percent=97.0),
            FakePlayer("B", rank_percent=80.0),
            FakePlayer("C", rank_percent=60.0),
        ),
    )
    forward = _candidates_for(backend)
    backward = list(reversed(forward))
    assert _run(forward) == _run(backward)


def test_same_population_produces_the_same_benchmark(tmp_path: Path) -> None:
    def _run(sub: Path) -> object:
        store, bstore, pstore = _env(sub)
        backend = FakeWclBackend()
        backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
        advance_benchmark_build(
            query_fn=backend.query_fn,
            client=_ample_client(),
            log_store=store,
            progress_store=pstore,
            benchmark_store=bstore,
            settings=_settings(),
            target=TARGET,
            policy=BenchmarkPolicy.default(),
            candidates=_candidates_for(backend),
        )
        bench = bstore.read_benchmark(TARGET.benchmark_id)
        store.close()
        return bench

    a = _run(tmp_path / "a")
    b = _run(tmp_path / "b")
    assert a == b


# -- 28-32: zero eventos de execução ------------------------------------------------


def test_no_execution_event_op_names_are_ever_issued(tmp_path: Path) -> None:
    store, bstore, pstore = _env(tmp_path)
    backend = FakeWclBackend()
    for i in range(3):
        backend.add_fight(f"rep{i}", 1, (FakePlayer(f"P{i}", rank_percent=60.0 + i),))
    advance_benchmark_build(
        query_fn=backend.query_fn,
        client=_ample_client(),
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=_settings(),
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
    )
    op_names = {k[0] for k in backend.query_log}
    forbidden = {
        "fetch_player_damage_events",
        "fetch_player_events",
        "fetch_player_resource_events",
        "fetch_player_buffs",
        "fetch_player_debuffs",
    }
    assert op_names.issubset({"fetch_report_rankings", "fetch_player_setup_only"})
    assert not (op_names & forbidden)


# -- 33/34: zero WCL real / Discord real ---------------------------------------------


def test_module_imports_no_discord_dependency() -> None:
    import inspect

    from botgitgud.analysis import benchmark_builder as module

    source = inspect.getsource(module)
    assert "import discord" not in source
    assert "discord." not in source


# -- cenário sintético obrigatório: 90 candidatos, 3 janelas (25/35/30) -------------


def _build_90_candidate_population() -> FakeWclBackend:
    """30 por banda, 3 jogadores por fight (10 fights/banda) — cada fight
    compartilhado por 3 candidatos, provando sharing dentro do cenário
    grande, não só em testes isolados.
    """
    backend = FakeWclBackend()
    band_ranges = {"p95-99": (95.0, 98.9), "p75-95": (75.0, 94.9), "p50-75": (50.0, 74.9)}
    idx = 0
    for band_name, (lo, hi) in band_ranges.items():
        step = (hi - lo) / 30
        for fight_i in range(10):
            players = []
            for slot in range(3):
                pct = lo + step * (fight_i * 3 + slot)
                players.append(FakePlayer(f"{band_name}-{idx}", rank_percent=pct))
                idx += 1
            backend.add_fight(f"rep-{band_name}-{fight_i}", 1, tuple(players))
    return backend


def test_synthetic_90_candidates_across_three_windows(tmp_path: Path) -> None:
    """90 candidatos / 3 bandas / 30 por banda, 3 janelas de orçamento
    (aproximando 25/35/30 do cenário obrigatório) — o `client` é ligado ao
    `backend` para que cada query REALMENTE decremente `points_remaining`
    (um client estático nunca esgota, então "affordar 9 fights" seria
    verdade em toda reavaliação dentro da mesma janela e o motor terminaria
    tudo numa chamada só, mascarando o resume).
    """
    store, bstore, pstore = _env(tmp_path)
    backend = _build_90_candidate_population()
    candidates = _candidates_for(backend)
    assert len(candidates) == 90
    settings = _settings(benchmark_build_chunk_fights=1000)
    policy = BenchmarkPolicy.default()
    floor = protected_floor(settings) + settings.benchmark_build_safety_margin

    # Cada fight serve 3 candidatos; 2 queries/fight * 5.0 pts/query
    # (backend.points_per_query) = 10.0 pts REALMENTE gastos por fight.
    backend.points_per_query = 5.0

    # Janela 1: orçamento para ~3 fights (~9 candidatos, próximo de 25/3).
    client = FakeBudgetClient(points_remaining=floor + 10.0 * 3.4)
    backend.client = client
    r1 = advance_benchmark_build(
        query_fn=backend.query_fn,
        client=client,
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=settings,
        target=TARGET,
        policy=policy,
        candidates=candidates,
    )
    assert r1.state is BenchmarkBuildState.DEFERRED_BUDGET
    assert 0 < r1.completed < 90
    completed_after_1 = r1.completed
    queries_after_1 = len(backend.query_log)

    # Janela 2: orçamento renovado para mais ~4 fights, ainda parcial.
    client.points_remaining = floor + 10.0 * 4.4
    r2 = advance_benchmark_build(
        query_fn=backend.query_fn,
        client=client,
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=settings,
        target=TARGET,
        policy=policy,
        candidates=candidates,
    )
    assert r2.completed > completed_after_1
    assert r2.completed < 90
    assert len(set(backend.query_log)) == len(backend.query_log)  # nenhum fight refetched

    # Janela 3: orçamento amplo — termina o resto.
    client.points_remaining = 1_000_000.0
    r3 = advance_benchmark_build(
        query_fn=backend.query_fn,
        client=client,
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=settings,
        target=TARGET,
        policy=policy,
        candidates=candidates,
    )
    assert r3.state is BenchmarkBuildState.READY
    assert r3.planned == 90
    assert r3.completed == 90

    # nenhum fight foi buscado mais de uma vez em nenhuma janela
    assert len(backend.query_log) == len(set(backend.query_log))
    # exatamente 2 queries por fight único (30 fights: 10/banda)
    assert len(backend.query_log) == 60

    benchmark = bstore.read_benchmark(TARGET.benchmark_id)
    assert benchmark is not None
    assert benchmark.bands["p95-99"].sample_size == 30
    assert benchmark.bands["p75-95"].sample_size == 30
    assert benchmark.bands["p50-75"].sample_size == 30
    assert benchmark.total_input_observations == 90
    assert queries_after_1 > 0  # janela 1 de fato fez trabalho de rede
    store.close()


def test_build_benchmark_until_budget_reaches_ready_in_one_call(tmp_path: Path) -> None:
    store, bstore, pstore = _env(tmp_path)
    backend = _build_90_candidate_population()
    result = build_benchmark_until_budget(
        query_fn=backend.query_fn,
        client=_ample_client(),
        log_store=store,
        progress_store=pstore,
        benchmark_store=bstore,
        settings=_settings(),
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
    )
    assert result.state is BenchmarkBuildState.READY
    assert result.completed == 90
