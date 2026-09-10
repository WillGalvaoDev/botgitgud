"""EB.6 — o gatilho de produção do `benchmark_build`.

Zero rede, zero Discord: reusa `FakeWclBackend` (tests/fixtures/
fake_wcl_backend.py) e a mesma forma de ambiente de EB.5, e nunca chama
`run_analysis` — o gatilho consome um `AnalysisResult` já pronto, que é
exatamente o contrato real (o pipeline devolve a identidade, `bot/` decide
o que fazer com ela).
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fake_wcl_backend import FakePlayer, FakeWclBackend
from pydantic import SecretStr

from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget
from botgitgud.analysis.benchmark_build_progress import BenchmarkBuildProgressStore
from botgitgud.analysis.benchmark_store import BenchmarkStore
from botgitgud.analysis.benchmark_store_models import policy_fingerprint
from botgitgud.analysis.pipeline import AnalysisResult, Deps
from botgitgud.bot.benchmark_job import dedup_key_for
from botgitgud.bot.benchmark_trigger import (
    maybe_enqueue_benchmark_build,
    resolve_current_population_size,
)
from botgitgud.bot.job_models import BudgetStatus, Job, now_utc_naive
from botgitgud.bot.jobs import JobQueue
from botgitgud.bot.worker import run_claimed_job
from botgitgud.config import Settings
from botgitgud.domain.models import RankingCandidate, RunManifest
from botgitgud.domain.specs import SpecId
from botgitgud.domain.spells import SpellCatalog
from botgitgud.ingest.log_fetcher import LogFetcher
from botgitgud.ingest.store import Store
from botgitgud.report.text import ReportHeader

TARGET = EncounterBenchmarkTarget(
    spec=SpecId("Warlock", "Demonology"), encounter_id=3179, difficulty=5, partition=3
)
COHORT_ID = "cohort-abc"


class _CountingClient:
    """Conta TODA query — o gatilho precisa gastar exatamente zero. Também
    satisfaz o `BudgetClient` mínimo, que `ensure_benchmark_job` nunca
    chega a consultar (freshness é só SQL local).
    """

    def __init__(self, backend: FakeWclBackend) -> None:
        self._backend = backend
        self.points_remaining = 1_000_000.0
        self.points_limit = 1_000_000.0
        self.points_reset_in: float | None = None
        self.queries: list[str] = []

    def query(self, query: str, variables: dict[str, object], *, op_name: str) -> dict[str, object]:
        self.queries.append(op_name)
        return self._backend.query_fn(query, variables, op_name=op_name)

    def refresh_budget(self) -> None:  # pragma: no cover - nunca alcançado pelo gatilho
        self.queries.append("refresh_budget")


def _settings(tmp_path: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        discord_token=SecretStr("x"),
        wcl_client_id=SecretStr("x"),
        wcl_client_secret=SecretStr("x"),
        blizzard_client_id=SecretStr("x"),
        blizzard_client_secret=SecretStr("x"),
        data_dir=tmp_path,
    )


def _env(tmp_path: Path) -> tuple[Deps, JobQueue, _CountingClient]:
    backend = FakeWclBackend()
    backend.add_fight("repA", 1, (FakePlayer("Alpha", rank_percent=97.0),))
    store = Store(tmp_path)
    catalog = SpellCatalog(tmp_path / "spells.json", blizzard=None)
    client = _CountingClient(backend)
    fetcher = LogFetcher(client, store, catalog)  # type: ignore[arg-type]
    deps = Deps(
        client=client,  # type: ignore[arg-type]
        fetcher=fetcher,
        store=store,
        catalog=catalog,
        settings=_settings(tmp_path),
    )
    return deps, JobQueue(store), client


def _claim(queue: JobQueue) -> Job | None:
    return queue.claim_next(BudgetStatus(points_remaining=1_000_000.0, limit_per_hour=1_000_000.0))


def _candidates(n: int = 3) -> list[RankingCandidate]:
    return [RankingCandidate("repA", i, f"P{i}", 300.0) for i in range(1, n + 1)]


def _manifest(cohort_id: str = COHORT_ID) -> RunManifest:
    return RunManifest(
        cohort_id=cohort_id,
        code_version="test",
        generated_at=datetime.now(UTC).replace(tzinfo=None),
        n_members=10,
        wcl_partition=3,
        settings_hash="h",
    )


def _result(
    *,
    target: EncounterBenchmarkTarget | None = TARGET,
    policy: BenchmarkPolicy | None = None,
    cohort_id: str = COHORT_ID,
) -> AnalysisResult:
    return AnalysisResult(
        header=ReportHeader(
            char_name="Alpha",
            boss_name="Boss",
            class_name="Warlock",
            spec="Demonology",
            reference_n=10,
            duration_min_s=280.0,
            duration_max_s=320.0,
        ),
        comparisons=(),
        manifest=_manifest(cohort_id),
        benchmark_target=target,
        benchmark_policy=BenchmarkPolicy.default() if policy is None else policy,
    )


def _seed_pool(deps: Deps, candidates: list[RankingCandidate], cohort_id: str = COHORT_ID) -> None:
    deps.store.write_candidate_pool(cohort_id, candidates)


def _write_benchmark_row(deps: Deps, *, source_count: int) -> None:
    """Escreve a linha de benchmark diretamente, com o
    `source_observation_count` desejado — o lado PERSISTIDO da freshness.
    Evita construir um `EncounterBenchmark` inteiro só para controlar um
    inteiro.
    """
    BenchmarkStore(deps.store)  # garante o DDL
    now = datetime.now(UTC).replace(tzinfo=None)
    deps.store.execute(
        """
        INSERT INTO encounter_benchmarks (
            benchmark_id, class_name, spec_name, encounter_id, difficulty, partition,
            benchmark_policy_version, created_at, updated_at,
            source_observation_count, eligible_observation_count,
            setup_available_count, setup_missing_count, deduped_count,
            population_fingerprint, policy_fingerprint, benchmark_payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            TARGET.benchmark_id,
            TARGET.spec.class_name,
            TARGET.spec.spec_name,
            TARGET.encounter_id,
            TARGET.difficulty,
            TARGET.partition,
            TARGET.benchmark_policy_version,
            now,
            now,
            source_count,
            source_count,
            source_count,
            0,
            0,
            "fp",
            policy_fingerprint(BenchmarkPolicy.default()),
            "{}",
        ],
    )


# -- 1: benchmark ausente -> enfileira ---------------------------------------------


def test_missing_benchmark_enqueues(tmp_path: Path) -> None:
    deps, queue, _client = _env(tmp_path)
    _seed_pool(deps, _candidates())

    outcome = maybe_enqueue_benchmark_build(deps=deps, queue=queue, result=_result(), source="t")
    assert outcome is not None
    assert outcome.created
    assert outcome.reason == "created"
    assert outcome.job is not None
    assert outcome.job.job_type == "benchmark_build"
    assert outcome.job.dedup_key == dedup_key_for(TARGET)


# -- 2: benchmark stale -> enfileira rebuild ---------------------------------------


def test_stale_benchmark_enqueues_rebuild(tmp_path: Path) -> None:
    deps, queue, _client = _env(tmp_path)
    _seed_pool(deps, _candidates(n=10))
    # 10 candidatos utilizáveis agora contra 2 observações persistidas:
    # crescimento de 400%, muito acima de stale_population_growth_ratio.
    _write_benchmark_row(deps, source_count=2)

    outcome = maybe_enqueue_benchmark_build(deps=deps, queue=queue, result=_result(), source="t")
    assert outcome is not None
    assert outcome.created


# -- 3: benchmark fresh -> nenhum enqueue ------------------------------------------


def test_fresh_benchmark_does_not_enqueue(tmp_path: Path) -> None:
    deps, queue, _client = _env(tmp_path)
    _seed_pool(deps, _candidates(n=3))
    _write_benchmark_row(deps, source_count=3)

    outcome = maybe_enqueue_benchmark_build(deps=deps, queue=queue, result=_result(), source="t")
    assert outcome is not None
    assert not outcome.created
    assert outcome.reason == "fresh"
    assert _claim(queue) is None


# -- 16: current_population_size semanticamente correto ----------------------------


def test_population_size_ignores_permanently_failed_candidates(tmp_path: Path) -> None:
    """O caso que causaria stale PERMANENTE se os dois lados da freshness
    medissem universos diferentes: 4 candidatos, 1 impossível de buscar.
    `source_observation_count` só pode chegar a 3, então a população
    "atual" também precisa ser 3 — senão (4-3)/3 = 0.33 > 0.30 e todo
    `!analisar` enfileiraria um rebuild sem nada para fazer.
    """
    deps, _queue, _client = _env(tmp_path)
    progress = BenchmarkBuildProgressStore(deps.store)
    candidates = _candidates(n=4)
    progress.register_candidates(TARGET.benchmark_id, candidates)
    for _ in range(3):  # MAX_CANDIDATE_ATTEMPTS
        progress.mark_failed(TARGET.benchmark_id, "repA", 4, "P4", error="unreachable")

    assert resolve_current_population_size(progress, target=TARGET, candidates=candidates) == 3


def test_permanently_failed_candidates_keep_the_benchmark_fresh(tmp_path: Path) -> None:
    """O mesmo cenário, agora fim-a-fim: com 3 de 4 candidatos buscáveis e
    um benchmark de 3 observações, nenhuma análise futura deve enfileirar
    nada. Este é o teste que falha se alguém trocar a medição por
    `len(candidates)`.
    """
    deps, queue, _client = _env(tmp_path)
    candidates = _candidates(n=4)
    _seed_pool(deps, candidates)
    progress = BenchmarkBuildProgressStore(deps.store)
    progress.register_candidates(TARGET.benchmark_id, candidates)
    for _ in range(3):
        progress.mark_failed(TARGET.benchmark_id, "repA", 4, "P4", error="unreachable")
    _write_benchmark_row(deps, source_count=3)

    for _ in range(3):
        outcome = maybe_enqueue_benchmark_build(
            deps=deps, queue=queue, result=_result(), source="t"
        )
        assert outcome is not None
        assert outcome.reason == "fresh"
    assert _claim(queue) is None


def test_population_size_unions_pools_from_other_duration_buckets(tmp_path: Path) -> None:
    """O pool é por `cohort_id` (inclui bucket de duração); o benchmark não.
    Um pool disjunto do mesmo tamanho traz 100% de população nova — a união
    com as linhas já registradas é o que torna isso visível.
    """
    deps, _queue, _client = _env(tmp_path)
    progress = BenchmarkBuildProgressStore(deps.store)
    progress.register_candidates(TARGET.benchmark_id, _candidates(n=3))
    other_bucket = [RankingCandidate("repB", i, f"Q{i}", 500.0) for i in range(1, 4)]

    assert resolve_current_population_size(progress, target=TARGET, candidates=other_bucket) == 6


def test_population_size_is_stable_when_pool_is_already_registered(tmp_path: Path) -> None:
    deps, _queue, _client = _env(tmp_path)
    progress = BenchmarkBuildProgressStore(deps.store)
    candidates = _candidates(n=5)
    progress.register_candidates(TARGET.benchmark_id, candidates)

    assert resolve_current_population_size(progress, target=TARGET, candidates=candidates) == 5
    assert resolve_current_population_size(progress, target=TARGET, candidates=[]) == 5


# -- 6/7/8/9: candidatos vêm do Store, custo zero ----------------------------------


def test_candidates_come_from_the_store_and_cost_no_wcl(tmp_path: Path) -> None:
    deps, queue, client = _env(tmp_path)
    candidates = _candidates(n=3)
    _seed_pool(deps, candidates)

    outcome = maybe_enqueue_benchmark_build(deps=deps, queue=queue, result=_result(), source="t")
    assert outcome is not None
    assert outcome.job is not None
    assert client.queries == []  # zero discovery, zero rankings, zero setup fetch

    payload = json.loads(outcome.job.payload_json or "{}")
    assert [c["player_name"] for c in payload["candidates"]] == [c.player_name for c in candidates]


def test_no_candidate_pool_skips_without_enqueue(tmp_path: Path) -> None:
    deps, queue, client = _env(tmp_path)
    outcome = maybe_enqueue_benchmark_build(deps=deps, queue=queue, result=_result(), source="t")
    assert outcome is None
    assert client.queries == []
    assert _claim(queue) is None


# -- 10/11: repetição e concorrência -> um único job --------------------------------


def test_repeated_analyses_produce_a_single_job(tmp_path: Path) -> None:
    deps, queue, _client = _env(tmp_path)
    _seed_pool(deps, _candidates())

    first = maybe_enqueue_benchmark_build(deps=deps, queue=queue, result=_result(), source="a")
    assert first is not None
    assert first.created
    assert first.job is not None

    for source in ("b", "c"):
        later = maybe_enqueue_benchmark_build(
            deps=deps, queue=queue, result=_result(), source=source
        )
        assert later is not None
        assert not later.created
        assert later.reason == "already_active"
        assert later.job is not None
        assert later.job.job_id == first.job.job_id

    claimed = _claim(queue)
    assert claimed is not None
    assert _claim(queue) is None


def test_concurrent_triggers_single_flight(tmp_path: Path) -> None:
    deps, queue, _client = _env(tmp_path)
    _seed_pool(deps, _candidates())
    created_flags: list[bool] = []
    lock = threading.Lock()
    barrier = threading.Barrier(4)

    def _go() -> None:
        barrier.wait()
        outcome = maybe_enqueue_benchmark_build(
            deps=deps, queue=queue, result=_result(), source="concurrent"
        )
        with lock:
            created_flags.append(bool(outcome is not None and outcome.created))

    threads = [threading.Thread(target=_go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(created_flags) == 1
    assert _claim(queue) is not None
    assert _claim(queue) is None


# -- 12/13/14/15: identidade correta no job -----------------------------------------


def test_job_payload_carries_target_policy_partition_and_spec(tmp_path: Path) -> None:
    deps, queue, _client = _env(tmp_path)
    _seed_pool(deps, _candidates())

    outcome = maybe_enqueue_benchmark_build(deps=deps, queue=queue, result=_result(), source="t")
    assert outcome is not None
    assert outcome.job is not None
    payload = json.loads(outcome.job.payload_json or "{}")

    assert EncounterBenchmarkTarget.from_dict(payload["target"]) == TARGET
    assert payload["target"]["partition"] == 3
    assert BenchmarkPolicy.from_dict(payload["policy"]) == BenchmarkPolicy.default()


def test_result_without_benchmark_identity_is_a_no_op(tmp_path: Path) -> None:
    deps, queue, _client = _env(tmp_path)
    _seed_pool(deps, _candidates())

    assert (
        maybe_enqueue_benchmark_build(
            deps=deps, queue=queue, result=_result(target=None), source="t"
        )
        is None
    )
    assert _claim(queue) is None


# -- 17: isolamento de falha --------------------------------------------------------


def test_enqueue_failure_never_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    deps, queue, _client = _env(tmp_path)
    _seed_pool(deps, _candidates())

    def _boom(**_kwargs: object) -> None:
        raise RuntimeError("fila indisponível")

    monkeypatch.setattr("botgitgud.bot.benchmark_trigger.ensure_benchmark_job", _boom)
    assert (
        maybe_enqueue_benchmark_build(deps=deps, queue=queue, result=_result(), source="t") is None
    )


def test_corrupt_progress_table_never_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps, queue, _client = _env(tmp_path)
    _seed_pool(deps, _candidates())

    def _boom(_self: object, _benchmark_id: str) -> list[object]:
        raise ValueError("linha de progresso corrompida")

    monkeypatch.setattr(BenchmarkBuildProgressStore, "read_progress", _boom)
    assert (
        maybe_enqueue_benchmark_build(deps=deps, queue=queue, result=_result(), source="t") is None
    )


# -- 20/21: prioridade preservada ---------------------------------------------------


def test_benchmark_build_stays_lowest_priority(tmp_path: Path) -> None:
    deps, queue, _client = _env(tmp_path)
    _seed_pool(deps, _candidates())

    maybe_enqueue_benchmark_build(deps=deps, queue=queue, result=_result(), source="t")
    queue.enqueue(
        job_type="build_cohort",
        dedup_key="cohort:3179:Warlock:Demonology:5:all",
        discord_user_id="u2",
        discord_channel_id="c1",
    )
    queue.enqueue(
        job_type="analyze", dedup_key="repZ:9:Zed", discord_user_id="u1", discord_channel_id="c1"
    )

    claimed = _claim(queue)
    assert claimed is not None
    assert claimed.job_type == "analyze"


# -- 4/5/18/19: os dois caminhos reais de produção ---------------------------------


def test_worker_path_enqueues_without_making_the_report_wait(tmp_path: Path) -> None:
    """19 (worker path) + 4 (a primeira análise não espera o benchmark) +
    5 (a resposta continua entregável): `run_claimed_job` roda a análise
    inteira offline, devolve o contrato de coaching, e SÓ então deixa um
    `benchmark_build` `queued` — nunca executado dentro desta chamada.
    """
    from test_pipeline import _build_deps, _DispatchTransport, _happy_path_responses

    deps = _build_deps(tmp_path, _DispatchTransport(_happy_path_responses()))
    store = Store(tmp_path / "queue_data")
    queue = JobQueue(store)
    job = Job(
        job_id="job-eb6",
        job_type="analyze",
        dedup_key="ABCDEFGHIJKLMNOP:1:Zarad",
        discord_user_id="user-1",
        discord_channel_id="chan-1",
        status="running",
        created_at=now_utc_naive(),
        started_at=now_utc_naive(),
        finished_at=None,
        error=None,
        report_path=None,
    )

    outcome = run_claimed_job(queue, job, deps)

    assert outcome.ok is True
    assert outcome.report_contract is not None
    assert not (deps.settings.data_dir / "reports").exists()

    queued = [j for j in queue.list_recent() if j.job_type == "benchmark_build"]
    assert len(queued) == 1
    assert queued[0].status == "queued"  # enfileirado, não executado nesta chamada
    store.close()


def test_repeated_worker_analyses_still_produce_a_single_benchmark_job(tmp_path: Path) -> None:
    from test_pipeline import _build_deps, _DispatchTransport, _happy_path_responses

    deps = _build_deps(tmp_path, _DispatchTransport(_happy_path_responses()))
    store = Store(tmp_path / "queue_data")
    queue = JobQueue(store)

    for n in (1, 2):
        run_claimed_job(
            queue,
            Job(
                job_id=f"job-{n}",
                job_type="analyze",
                dedup_key="ABCDEFGHIJKLMNOP:1:Zarad",
                discord_user_id="user-1",
                discord_channel_id="chan-1",
                status="running",
                created_at=now_utc_naive(),
                started_at=now_utc_naive(),
                finished_at=None,
                error=None,
                report_path=None,
            ),
            deps,
        )

    assert len([j for j in queue.list_recent() if j.job_type == "benchmark_build"]) == 1
    store.close()


def test_discord_direct_path_triggers_after_delivery() -> None:
    """18 (Discord direct path). O handler `cmd_analisar` é glue assíncrono
    fora da cobertura unitária (docs/desvios.md D-22), então a prova é
    estrutural, via AST — a mesma técnica que `test_report_contract.py` já
    usa para provar um call site: a chamada existe, e vem DEPOIS de
    `deliver_completed_report` (CL.5; antigo `send_report`) no corpo da
    função.
    """
    import ast
    import inspect

    import botgitgud.bot.discord_bot as discord_module

    tree = ast.parse(inspect.getsource(discord_module))
    handler = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "cmd_analisar"
    )
    called = {
        node.func.id
        for node in ast.walk(handler)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "maybe_enqueue_benchmark_build" in called

    def _line_of(name: str) -> int:
        return max(
            node.lineno
            for node in ast.walk(handler)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == name
        )

    assert _line_of("maybe_enqueue_benchmark_build") > _line_of("deliver_completed_report")


def test_worker_path_call_site_exists() -> None:
    import ast
    import inspect

    import botgitgud.bot.worker as worker_module

    tree = ast.parse(inspect.getsource(worker_module))
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "run_claimed_job"
    )
    called = {
        node.func.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "maybe_enqueue_benchmark_build" in called


def test_analysis_pipeline_never_imports_the_job_queue() -> None:
    """A regra de camada que motivou EB.6 viver em `bot/`: se um dia alguém
    puser `JobQueue` dentro de `analysis/pipeline.py`, este teste falha.
    """
    import ast
    import inspect

    import botgitgud.analysis.pipeline as pipeline_module

    tree = ast.parse(inspect.getsource(pipeline_module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    assert not any("bot." in name for name in imported), imported

    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "ensure_benchmark_job" not in called
