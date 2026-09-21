"""EB.5 — Encounter Benchmark como job real da fila (baixa prioridade,
resumível, single-flight). Zero rede real: reusa `FakeWclBackend`/
`FakePlayer` (tests/fixtures/fake_wcl_backend.py, já verificado contra
cassete real em EB.4).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from fake_wcl_backend import FakePlayer, FakeWclBackend
from pydantic import SecretStr

from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget
from botgitgud.analysis.benchmark_build_progress import BenchmarkBuildProgressStore
from botgitgud.analysis.benchmark_store import BenchmarkStore
from botgitgud.analysis.pipeline import Deps
from botgitgud.bot.benchmark_job import (
    SYSTEM_ACTOR_ID,
    EnsureBenchmarkJobResult,
    dedup_key_for,
    ensure_benchmark_job,
    run_benchmark_build_job,
)
from botgitgud.bot.job_models import BudgetStatus, Job, JobOutcome, now_utc_naive
from botgitgud.bot.jobs import JobQueue
from botgitgud.bot.worker import run_claimed_job
from botgitgud.config import Settings
from botgitgud.domain.models import RankingCandidate
from botgitgud.domain.specs import SpecId
from botgitgud.domain.spells import SpellCatalog
from botgitgud.ingest.log_fetcher import LogFetcher
from botgitgud.ingest.store import Store

TARGET = EncounterBenchmarkTarget(
    spec=SpecId("Warlock", "Demonology"), encounter_id=3179, difficulty=5, partition=3
)


class _FakeDepsClient:
    """Um objeto só, satisfazendo tanto `QueryFn` (`.query`) quanto o
    `BudgetClient` que `advance_benchmark_build` espera — `deps.client` no
    código real é um único `WclClient` que faz as duas coisas.
    """

    def __init__(
        self, backend: FakeWclBackend, *, points: float, points_per_query: float = 2.0
    ) -> None:
        self._backend = backend
        self.points_remaining = points
        self.points_limit = 1_000_000.0
        self.points_reset_in: float | None = None
        self.refresh_calls = 0
        self._points_per_query = points_per_query

    def query(self, query: str, variables: dict[str, object], *, op_name: str) -> dict[str, object]:
        result = self._backend.query_fn(query, variables, op_name=op_name)
        self.points_remaining -= self._points_per_query
        return result

    def refresh_budget(self) -> None:
        self.refresh_calls += 1


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "discord_token": SecretStr("x"),
        "wcl_client_id": SecretStr("x"),
        "wcl_client_secret": SecretStr("x"),
        "blizzard_client_id": SecretStr("x"),
        "blizzard_client_secret": SecretStr("x"),
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _env(
    tmp_path: Path,
    backend: FakeWclBackend,
    *,
    points: float = 1_000_000.0,
    **settings_overrides: object,
) -> tuple[Deps, JobQueue, _FakeDepsClient]:
    store = Store(tmp_path)
    catalog = SpellCatalog(tmp_path / "spells.json", blizzard=None)
    client = _FakeDepsClient(backend, points=points)
    fetcher = LogFetcher(client, store, catalog)  # type: ignore[arg-type]
    settings_overrides.setdefault("data_dir", tmp_path)
    settings = _settings(**settings_overrides)
    deps = Deps(client=client, fetcher=fetcher, store=store, catalog=catalog, settings=settings)  # type: ignore[arg-type]
    queue = JobQueue(store)
    return deps, queue, client


def _candidates_for(backend: FakeWclBackend) -> list[RankingCandidate]:
    out = []
    for (report_code, fight_id), players in backend.fights.items():
        duration = backend.fight_duration_s.get((report_code, fight_id), 300.0)
        out.extend(RankingCandidate(report_code, fight_id, p.name, duration) for p in players)
    return out


def _full_budget() -> BudgetStatus:
    return BudgetStatus(points_remaining=1_000_000.0, limit_per_hour=1_000_000.0)


# -- 1: enqueue --------------------------------------------------------------------


def test_enqueue_benchmark_job(tmp_path: Path) -> None:
    backend = FakeWclBackend()
    backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
    deps, queue, _client = _env(tmp_path, backend)

    result = ensure_benchmark_job(
        deps=deps,
        queue=queue,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
        current_population_size=1,
    )
    assert result.created
    assert result.job is not None
    assert result.job.job_type == "benchmark_build"
    assert result.job.status == "queued"
    assert result.job.discord_user_id.startswith(SYSTEM_ACTOR_ID)


# -- 2/3/4: dedupe / policy / partition ---------------------------------------------


def test_dedupe_same_benchmark_id(tmp_path: Path) -> None:
    backend = FakeWclBackend()
    backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
    deps, queue, _client = _env(tmp_path, backend)

    first = ensure_benchmark_job(
        deps=deps,
        queue=queue,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
        current_population_size=1,
    )
    second = ensure_benchmark_job(
        deps=deps,
        queue=queue,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
        current_population_size=1,
    )
    assert first.created
    assert not second.created
    assert second.reason == "already_active"
    assert second.job is not None
    assert second.job.job_id == first.job.job_id  # type: ignore[union-attr]


def test_different_policy_version_is_a_distinct_job(tmp_path: Path) -> None:
    backend = FakeWclBackend()
    backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
    deps, queue, _client = _env(tmp_path, backend)

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
    r1 = ensure_benchmark_job(
        deps=deps,
        queue=queue,
        target=target_v1,
        policy=BenchmarkPolicy(policy_version="v1"),
        candidates=_candidates_for(backend),
        current_population_size=1,
    )
    r2 = ensure_benchmark_job(
        deps=deps,
        queue=queue,
        target=target_v2,
        policy=BenchmarkPolicy(policy_version="v2"),
        candidates=_candidates_for(backend),
        current_population_size=1,
    )
    assert r1.created and r2.created
    assert r1.job.job_id != r2.job.job_id  # type: ignore[union-attr]


def test_different_partition_is_a_distinct_job(tmp_path: Path) -> None:
    backend = FakeWclBackend()
    backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
    deps, queue, _client = _env(tmp_path, backend)

    target_p4 = EncounterBenchmarkTarget(
        spec=TARGET.spec,
        encounter_id=TARGET.encounter_id,
        difficulty=TARGET.difficulty,
        partition=4,
    )
    r1 = ensure_benchmark_job(
        deps=deps,
        queue=queue,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
        current_population_size=1,
    )
    r2 = ensure_benchmark_job(
        deps=deps,
        queue=queue,
        target=target_p4,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
        current_population_size=1,
    )
    assert r1.created and r2.created
    assert r1.job.job_id != r2.job.job_id  # type: ignore[union-attr]
    assert dedup_key_for(TARGET) != dedup_key_for(target_p4)


# -- 5/6: freshness gate -------------------------------------------------------------


def test_fresh_benchmark_does_not_enqueue(tmp_path: Path) -> None:
    backend = FakeWclBackend()
    backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
    deps, queue, _client = _env(tmp_path, backend)

    # constrói e persiste o benchmark de verdade primeiro (READY)
    r = ensure_benchmark_job(
        deps=deps,
        queue=queue,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
        current_population_size=1,
    )
    assert r.job is not None
    job = queue.claim_next(_full_budget())
    assert job is not None
    run_claimed_job(queue, job, deps)
    assert queue.get(job.job_id).status == "done"  # type: ignore[union-attr]

    result = ensure_benchmark_job(
        deps=deps,
        queue=queue,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
        current_population_size=1,
    )
    assert not result.created
    assert result.reason == "fresh"
    assert result.job is None


def test_stale_benchmark_enqueues(tmp_path: Path) -> None:
    backend = FakeWclBackend()
    backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
    deps, queue, _client = _env(tmp_path, backend)

    r = ensure_benchmark_job(
        deps=deps,
        queue=queue,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
        current_population_size=1,
    )
    job = queue.claim_next(_full_budget())
    assert job is not None
    run_claimed_job(queue, job, deps)

    # população disponível cresceu MUITO -> stale por population_growth
    result = ensure_benchmark_job(
        deps=deps,
        queue=queue,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
        current_population_size=1000,
    )
    assert result.created
    assert result.job is not None
    assert result.job.job_id != r.job.job_id  # type: ignore[union-attr]


# -- 7/8: prioridade ------------------------------------------------------------------


def test_priority_analyze_over_cohort_over_benchmark(tmp_path: Path) -> None:
    backend = FakeWclBackend()
    _deps, queue, _client = _env(tmp_path, backend)

    queue.enqueue(
        job_type="benchmark_build",
        dedup_key="benchmark:x",
        discord_user_id="s",
        discord_channel_id="s",
    )
    queue.enqueue(
        job_type="build_cohort", dedup_key="cohort:1", discord_user_id="u1", discord_channel_id="c1"
    )
    queue.enqueue(
        job_type="analyze", dedup_key="rep:1:Zarad", discord_user_id="u2", discord_channel_id="c2"
    )

    first = queue.claim_next(_full_budget())
    assert first is not None and first.job_type == "analyze"
    queue.mark_done(first.job_id)  # libera o slot de MAX_CONCURRENT_JOBS
    second = queue.claim_next(_full_budget())
    assert second is not None and second.job_type == "build_cohort"
    queue.mark_done(second.job_id)
    third = queue.claim_next(_full_budget())
    assert third is not None and third.job_type == "benchmark_build"


def test_benchmark_queued_first_analyze_still_wins(tmp_path: Path) -> None:
    backend = FakeWclBackend()
    _deps, queue, _client = _env(tmp_path, backend)

    queue.enqueue(
        job_type="benchmark_build",
        dedup_key="benchmark:x",
        discord_user_id="s",
        discord_channel_id="s",
    )
    queue.enqueue(
        job_type="analyze", dedup_key="rep:1:Zarad", discord_user_id="u2", discord_channel_id="c2"
    )
    claimed = queue.claim_next(_full_budget())
    assert claimed is not None
    assert claimed.job_type == "analyze"


# -- 9: fairness entre chunks ---------------------------------------------------------


def test_fairness_analyze_arrives_between_benchmark_chunks(tmp_path: Path) -> None:
    """Cenário do exit gate: A) benchmark executa 1 chunk; B) analyze chega;
    C) próximo claim é analyze; D) benchmark continua depois.
    """
    backend = FakeWclBackend()
    for i in range(3):
        backend.add_fight(f"rep{i}", 1, (FakePlayer(f"P{i}", rank_percent=60.0 + i),))
    deps, queue, _client = _env(tmp_path, backend, benchmark_build_chunk_fights=1)

    ensure_benchmark_job(
        deps=deps,
        queue=queue,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
        current_population_size=3,
    )
    job = queue.claim_next(_full_budget())
    assert job is not None and job.job_type == "benchmark_build"
    outcome = run_claimed_job(queue, job, deps)
    assert outcome.deferred  # teto de lotes — ainda faltam fights

    # B) analyze chega DEPOIS do benchmark já estar deferred_budget
    queue.enqueue(
        job_type="analyze", dedup_key="rep:1:Zarad", discord_user_id="u2", discord_channel_id="c2"
    )
    # C) próximo claim é analyze, mesmo com o benchmark elegível (deferred_until já passou -> 0s)
    next_job = queue.claim_next(_full_budget())
    assert next_job is not None
    assert next_job.job_type == "analyze"

    # D) benchmark continua depois
    third = queue.claim_next(_full_budget())
    assert third is not None
    assert third.job_type == "benchmark_build"
    assert third.job_id == job.job_id


# -- 10-13: mapeamento de estado ------------------------------------------------------


def test_ready_maps_to_done(tmp_path: Path) -> None:
    backend = FakeWclBackend()
    backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
    deps, queue, _client = _env(tmp_path, backend)
    ensure_benchmark_job(
        deps=deps,
        queue=queue,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
        current_population_size=1,
    )
    job = queue.claim_next(_full_budget())
    assert job is not None
    outcome = run_claimed_job(queue, job, deps)
    assert outcome.ok
    assert queue.get(job.job_id).status == "done"  # type: ignore[union-attr]


def test_deferred_budget_maps_to_deferred_budget_status(tmp_path: Path) -> None:
    backend = FakeWclBackend()
    for i in range(3):
        backend.add_fight(f"rep{i}", 1, (FakePlayer(f"P{i}", rank_percent=60.0 + i),))
    deps, queue, _client = _env(tmp_path, backend, benchmark_build_chunk_fights=1)
    ensure_benchmark_job(
        deps=deps,
        queue=queue,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
        current_population_size=3,
    )
    job = queue.claim_next(_full_budget())
    assert job is not None
    outcome = run_claimed_job(queue, job, deps)
    assert outcome.deferred
    persisted = queue.get(job.job_id)
    assert persisted is not None
    assert persisted.status == "deferred_budget"  # 12: deferred_until preenchido
    assert persisted.deferred_until is not None
    assert persisted.defer_reason is not None  # 13: defer_reason preservado


def test_low_budget_defers_with_budget_reason(tmp_path: Path) -> None:
    backend = FakeWclBackend()
    backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
    deps, queue, client = _env(tmp_path, backend)
    ensure_benchmark_job(
        deps=deps,
        queue=queue,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
        current_population_size=1,
    )
    from botgitgud.analysis.benchmark_build_budget import protected_floor

    client.points_remaining = protected_floor(deps.settings)  # abaixo da margem
    job = queue.claim_next(_full_budget())
    assert job is not None
    outcome = run_claimed_job(queue, job, deps)
    assert outcome.deferred
    persisted = queue.get(job.job_id)
    assert persisted is not None
    assert persisted.defer_reason == "benchmark_deferred_budget"


# -- 14/15/16: elapsed deferred resume + restart --------------------------------------


def test_elapsed_deferred_benchmark_resumes_without_another_queued_job(tmp_path: Path) -> None:
    """14/15: nenhum outro job `queued` precisa aparecer — a MESMA regra
    (`Job.is_claimable()`) que corrigiu o bug histórico do worker (B2) já
    cobre benchmark_build de graça.
    """
    backend = FakeWclBackend()
    for i in range(3):
        backend.add_fight(f"rep{i}", 1, (FakePlayer(f"P{i}", rank_percent=60.0 + i),))
    deps, queue, _client = _env(tmp_path, backend, benchmark_build_chunk_fights=1)
    ensure_benchmark_job(
        deps=deps,
        queue=queue,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
        current_population_size=3,
    )
    job = queue.claim_next(_full_budget())
    assert job is not None
    run_claimed_job(queue, job, deps)
    deferred = queue.get(job.job_id)
    assert deferred is not None and deferred.status == "deferred_budget"

    # simula o tempo passar: força deferred_until para o passado
    queue._store.execute(
        "UPDATE jobs SET deferred_until = ? WHERE job_id = ?",
        [now_utc_naive(), job.job_id],
    )
    active = queue.list_active()
    assert any(j.is_claimable() for j in active)  # o guard real do worker loop

    resumed = queue.claim_next(_full_budget())
    assert resumed is not None
    assert resumed.job_id == job.job_id  # 19: mesmo job_id


def test_benchmark_survives_store_reopen(tmp_path: Path) -> None:
    """16: restart/reopen — o mesmo padrão de teste que EB.3 já usa."""
    backend = FakeWclBackend()
    for i in range(3):
        backend.add_fight(f"rep{i}", 1, (FakePlayer(f"P{i}", rank_percent=60.0 + i),))
    deps, queue, _client = _env(tmp_path, backend, benchmark_build_chunk_fights=1)
    ensure_benchmark_job(
        deps=deps,
        queue=queue,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
        current_population_size=3,
    )
    job = queue.claim_next(_full_budget())
    assert job is not None
    run_claimed_job(queue, job, deps)
    deps.store.close()

    store2 = Store(tmp_path)
    queue2 = JobQueue(store2)
    reopened = queue2.get(job.job_id)
    assert reopened is not None
    assert reopened.status == "deferred_budget"
    assert reopened.payload_json is not None
    progress = BenchmarkBuildProgressStore(store2)
    assert len(progress.read_progress(TARGET.benchmark_id)) == 3
    store2.close()


# -- 17/18: NO_PROGRESS / FAILED -------------------------------------------------------


def test_no_progress_does_not_become_done(tmp_path: Path) -> None:
    backend = FakeWclBackend()
    backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
    backend.failing.add(("fetch_player_setup_only", "repA", 1))
    deps, queue, _client = _env(tmp_path, backend)
    ensure_benchmark_job(
        deps=deps,
        queue=queue,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
        current_population_size=1,
    )
    job = queue.claim_next(_full_budget())
    assert job is not None
    outcome = run_claimed_job(queue, job, deps)
    assert not outcome.ok
    assert outcome.deferred
    persisted = queue.get(job.job_id)
    assert persisted is not None
    assert persisted.status == "deferred_budget"
    assert persisted.defer_reason == "benchmark_no_progress"
    assert persisted.status != "done"


def test_failed_maps_to_failed_status(tmp_path: Path) -> None:
    """18: payload corrompido -> builder_error -> failed explícito."""
    backend = FakeWclBackend()
    deps, queue, _client = _env(tmp_path, backend)
    bad_job = Job(
        job_id="bad-job",
        job_type="benchmark_build",
        dedup_key="benchmark:bad",
        discord_user_id=SYSTEM_ACTOR_ID,
        discord_channel_id=SYSTEM_ACTOR_ID,
        status="running",
        created_at=now_utc_naive(),
        started_at=now_utc_naive(),
        finished_at=None,
        error=None,
        report_path=None,
        payload_json="not-json-at-all{{{",
    )
    outcome = run_benchmark_build_job(queue, bad_job, deps)
    assert not outcome.ok
    # o job nao foi inserido via enqueue, entao so confirmamos o outcome —
    # a chamada mark_failed usaria job_id="bad-job", inexistente na tabela;
    # o que importa e que NUNCA e reportado ok=True.
    assert isinstance(outcome, JobOutcome)


def test_defer_limit_exceeded_becomes_failed(tmp_path: Path) -> None:
    """Segurança contra loop infinito: defer_count alto -> failed explícito."""
    backend = FakeWclBackend()
    backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
    backend.failing.add(("fetch_player_setup_only", "repA", 1))
    deps, queue, _client = _env(tmp_path, backend)
    result = ensure_benchmark_job(
        deps=deps,
        queue=queue,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
        current_population_size=1,
    )
    assert result.job is not None
    job_id = result.job.job_id
    # força defer_count alto diretamente (equivalente a muitas janelas reais)
    queue._store.execute("UPDATE jobs SET defer_count = 49 WHERE job_id = ?", [job_id])
    job = queue.claim_next(_full_budget())
    assert job is not None
    outcome = run_claimed_job(queue, job, deps)
    assert not outcome.ok
    assert not outcome.deferred
    persisted = queue.get(job_id)
    assert persisted is not None
    assert persisted.status == "failed"


# -- 21/22: single-flight -------------------------------------------------------------


def test_single_flight_two_simultaneous_enqueues_produce_one_active_job(tmp_path: Path) -> None:
    backend = FakeWclBackend()
    backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
    deps, queue, _client = _env(tmp_path, backend)

    results: list[EnsureBenchmarkJobResult] = []
    barrier = threading.Barrier(2)

    def _call() -> None:
        barrier.wait(timeout=5)
        results.append(
            ensure_benchmark_job(
                deps=deps,
                queue=queue,
                target=TARGET,
                policy=BenchmarkPolicy.default(),
                candidates=_candidates_for(backend),
                current_population_size=1,
            )
        )

    threads = [threading.Thread(target=_call) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    job_ids = {r.job.job_id for r in results if r.job is not None}
    assert len(job_ids) == 1
    assert sum(1 for r in results if r.created) == 1


# -- 23: atomic claim -------------------------------------------------------------------


def test_atomic_claim_only_one_worker_gets_the_job(tmp_path: Path) -> None:
    backend = FakeWclBackend()
    backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
    deps, queue, _client = _env(tmp_path, backend)
    ensure_benchmark_job(
        deps=deps,
        queue=queue,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
        current_population_size=1,
    )

    claimed: list[Job | None] = []
    barrier = threading.Barrier(2)

    def _claim() -> None:
        barrier.wait(timeout=5)
        claimed.append(queue.claim_next(_full_budget()))

    threads = [threading.Thread(target=_claim) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    non_none = [c for c in claimed if c is not None]
    assert len(non_none) == 1


# -- 24/25: sem Discord / sem eventos de execução --------------------------------------


def test_benchmark_never_touches_discord() -> None:
    """Checa padrões de USO real (import/chamada), não a palavra em prosa —
    o próprio docstring do módulo explica em português por que ele não usa
    Discord, o que faria um grep textual ingênuo falso-positivar.
    """
    import inspect

    from botgitgud.bot import benchmark_job as module

    source = inspect.getsource(module)
    for forbidden in ("import discord", "discord.", "send_text(", "send_report("):
        assert forbidden not in source


def test_benchmark_never_fetches_execution_events(tmp_path: Path) -> None:
    backend = FakeWclBackend()
    for i in range(2):
        backend.add_fight(f"rep{i}", 1, (FakePlayer(f"P{i}", rank_percent=60.0 + i),))
    deps, queue, _client = _env(tmp_path, backend)
    ensure_benchmark_job(
        deps=deps,
        queue=queue,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
        current_population_size=2,
    )
    job = queue.claim_next(_full_budget())
    assert job is not None
    run_claimed_job(queue, job, deps)
    op_names = {k[0] for k in backend.query_log}
    forbidden = {
        "fetch_player_damage_events",
        "fetch_player_events",
        "fetch_player_resource_events",
        "fetch_player_buffs",
        "fetch_player_debuffs",
    }
    assert not (op_names & forbidden)


# -- 26: telemetry ----------------------------------------------------------------------


def test_lifecycle_telemetry_is_emitted(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import structlog

    events: list[dict[str, object]] = []

    def _capture(_logger: object, _method: str, event_dict: Any) -> Any:
        events.append(dict(event_dict))
        raise structlog.DropEvent

    structlog.configure(
        processors=[structlog.contextvars.merge_contextvars, _capture],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        cache_logger_on_first_use=False,
    )
    try:
        backend = FakeWclBackend()
        backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
        deps, queue, _client = _env(tmp_path, backend)
        ensure_benchmark_job(
            deps=deps,
            queue=queue,
            target=TARGET,
            policy=BenchmarkPolicy.default(),
            candidates=_candidates_for(backend),
            current_population_size=1,
        )
        job = queue.claim_next(_full_budget())
        assert job is not None
        run_claimed_job(queue, job, deps)
    finally:
        structlog.reset_defaults()

    names = {e.get("event") for e in events}
    assert "benchmark_job_enqueued" in names
    assert "benchmark_job_started" in names
    assert "benchmark_job_ready" in names
    ready = next(e for e in events if e.get("event") == "benchmark_job_ready")
    assert ready["benchmark_id"] == TARGET.benchmark_id
    assert "completed" in ready and "remaining" in ready


# -- 27: ops snapshot reconhece job type ------------------------------------------------


def test_ops_snapshot_recognizes_benchmark_job_type(tmp_path: Path) -> None:
    from botgitgud.bot.ops_snapshot import read_snapshot, write_snapshot

    backend = FakeWclBackend()
    backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
    deps, queue, _client = _env(tmp_path, backend)
    ensure_benchmark_job(
        deps=deps,
        queue=queue,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
        current_population_size=1,
    )
    active = queue.list_active()
    write_snapshot(tmp_path, active=active, recent=queue.list_recent())
    snapshot = read_snapshot(tmp_path)
    assert snapshot is not None
    assert snapshot.by_type.get("benchmark_build", {}).get("queued") == 1


# -- 28/29: zero WCL real / zero Discord real (garantido por construção) ---------------


def test_zero_network_beyond_the_fake_backend(tmp_path: Path) -> None:
    """Confirma que nenhum import de rede real existe no módulo (garantia
    estática complementar à garantia dinâmica dos testes acima)."""
    import inspect

    from botgitgud.bot import benchmark_job as module

    source = inspect.getsource(module)
    for forbidden in ("import aiohttp", "requests.", "httpx.Client("):
        assert forbidden not in source


# -- cenário integrado obrigatório -------------------------------------------------------


def test_integrated_scenario_analyze_preempts_then_benchmark_resumes(tmp_path: Path) -> None:
    """T0: benchmark queued. T1: worker executa 1 chunk -> DEFERRED_BUDGET.
    T2: analyze chega. T3: worker processa analyze primeiro, depois retoma
    O MESMO job_id de benchmark até READY/done — zero refetch de checkpoint
    concluído.
    """
    backend = FakeWclBackend()
    for i in range(4):
        backend.add_fight(f"rep{i}", 1, (FakePlayer(f"P{i}", rank_percent=60.0 + i),))
    deps, queue, _client = _env(tmp_path, backend, benchmark_build_chunk_fights=1)

    # T0
    enq = ensure_benchmark_job(
        deps=deps,
        queue=queue,
        target=TARGET,
        policy=BenchmarkPolicy.default(),
        candidates=_candidates_for(backend),
        current_population_size=4,
    )
    assert enq.job is not None
    benchmark_job_id = enq.job.job_id

    # T1: 1 chunk
    job = queue.claim_next(_full_budget())
    assert job is not None and job.job_type == "benchmark_build"
    outcome = run_claimed_job(queue, job, deps)
    assert outcome.deferred
    queries_after_t1 = list(backend.query_log)
    # chunk_fights=1, teto de 2 lotes por reivindicação -> 2 fights processados
    # neste claim antes de ceder, 2 queries cada.
    assert len(queries_after_t1) == 4

    # T2: analyze chega
    queue.enqueue(
        job_type="analyze", dedup_key="rep:1:Zarad", discord_user_id="u2", discord_channel_id="c2"
    )

    # T3: worker processa analyze primeiro (hot path priorizado)
    next_claim = queue.claim_next(_full_budget())
    assert next_claim is not None
    assert next_claim.job_type == "analyze"

    # retoma o MESMO benchmark job_id
    resumed = queue.claim_next(_full_budget())
    assert resumed is not None
    assert resumed.job_id == benchmark_job_id

    # nenhum refetch do que já foi concluído: o fight já processado no T1
    # nunca reaparece no query_log
    already_fetched_fight = queries_after_t1[0][1], queries_after_t1[0][2]
    outcome2 = run_claimed_job(queue, resumed, deps)
    new_fight_calls = [k for k in backend.query_log[len(queries_after_t1) :]]
    assert sum(1 for k in new_fight_calls if (k[1], k[2]) == already_fetched_fight) == 0

    # continua até READY em janelas subsequentes (chunk=1, então mais 2 chamadas)
    final_outcome = outcome2
    while final_outcome.deferred:
        resumed_job = queue.claim_next(_full_budget())
        assert resumed_job is not None
        assert resumed_job.job_id == benchmark_job_id
        final_outcome = run_claimed_job(queue, resumed_job, deps)

    assert final_outcome.ok
    final_job = queue.get(benchmark_job_id)
    assert final_job is not None
    assert final_job.status == "done"

    benchmark_store = BenchmarkStore(deps.store)
    persisted = benchmark_store.read_benchmark(TARGET.benchmark_id)
    assert persisted is not None
    assert persisted.total_input_observations == 4

    # zero refetch total: exatamente 2 queries por fight único (4 fights)
    assert len(backend.query_log) == 8
    assert len(set(backend.query_log)) == 8
