"""CL.0 — teto persistente de defers para cold cohort (`CohortDeferredBudget`).

Reproduz semanticamente o incidente real do job `2b3a1091d00c...`: um cold
cohort com candidatos permanentemente inatingíveis (o "Anonymous" que a WCL
às vezes anonimiza) deferia (`deferred_budget`/NO_PROGRESS) para sempre, sem
nunca virar READY nem `failed` — cada retomada refazia a descoberta do
leaderboard do zero (nenhum candidate pool persistido até READY) e uma
retomada real chegou a gastar ~1700 pontos WCL só reconstruindo progresso.

Dois grupos de teste:

- **Mecanismo do teto** (`test_below_threshold_*` até `test_hot_cohort_*`):
  usam o mesmo duplo de exceção que `test_worker.py`'s `_deferred_error()`
  já usa para B2 — provam a POLÍTICA de `_defer_job` isoladamente, rápido
  e sem simular WCL nenhuma.
- **Regressão principal** (`test_regression_*`): roda `run_claimed_job` de
  verdade, sem mockar `run_analysis`/`build_cohorts`, contra um transporte
  HTTP falso mas IDEMPOTENTE — cada `report_code` sempre responde igual,
  então o MESMO job pode ser reivindicado repetidamente exatamente como o
  worker faria após vários wakeups, sem esgotar uma fila de fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_pipeline import _buffs_response, _build_deps, _DispatchTransport
from test_worker import _deferred_error

import botgitgud.bot.worker as worker_module
from botgitgud.bot.benchmark_job import _MAX_CONSECUTIVE_DEFERS
from botgitgud.bot.job_models import BudgetStatus, now_utc_naive
from botgitgud.bot.jobs import JobQueue
from botgitgud.bot.worker import MAX_COLD_COHORT_DEFERS, run_claimed_job
from botgitgud.ingest.store import Store

ANALYZE_DEDUP_KEY = "ABCDEFGHIJKLMNOP:1:Zarad"
BUDGET = BudgetStatus(points_remaining=3600.0, limit_per_hour=3600.0)


def _enqueue_analyze(queue: JobQueue, *, dedup_key: str = ANALYZE_DEDUP_KEY) -> str:
    result = queue.enqueue(
        job_type="analyze",
        dedup_key=dedup_key,
        discord_user_id="user-1",
        discord_channel_id="chan-1",
    )
    assert result.job is not None
    return result.job.job_id


def _always_defers(monkeypatch: pytest.MonkeyPatch, **error_kwargs: object) -> None:
    """`run_analysis` sempre levanta `CohortDeferredBudget` — o duplo que
    `test_worker.py::_deferred_error` já estabelece para B2.
    """
    monkeypatch.setattr(
        worker_module,
        "run_analysis",
        lambda *_a, **_k: (_ for _ in ()).throw(_deferred_error(**error_kwargs)),  # type: ignore[arg-type]
    )


# ==================================================================================
# GRUPO A — mecanismo do teto (duplo de exceção, sem WCL)
# ==================================================================================


def test_below_threshold_stays_deferred_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _always_defers(monkeypatch)
    deps = _build_deps(tmp_path, _DispatchTransport({}))
    store = Store(tmp_path / "queue_data")
    queue = JobQueue(store)
    job_id = _enqueue_analyze(queue)

    claimed = queue.claim_next(BUDGET)
    assert claimed is not None
    outcome = run_claimed_job(queue, claimed, deps)

    assert outcome.deferred is True
    assert outcome.ok is False
    job = queue.get(job_id)
    assert job is not None
    assert job.status == "deferred_budget"
    assert job.defer_count == 1
    store.close()


def test_defer_count_persists_across_successive_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _always_defers(monkeypatch)
    deps = _build_deps(tmp_path, _DispatchTransport({}), cold_build_defer_retry_s=0.0)
    store = Store(tmp_path / "queue_data")
    queue = JobQueue(store)
    job_id = _enqueue_analyze(queue)

    observed_counts: list[int] = []
    for _ in range(MAX_COLD_COHORT_DEFERS - 1):
        claimed = queue.claim_next(BUDGET)
        assert claimed is not None
        run_claimed_job(queue, claimed, deps)
        job = queue.get(job_id)
        assert job is not None
        observed_counts.append(job.defer_count)

    assert observed_counts == list(range(1, MAX_COLD_COHORT_DEFERS))
    store.close()


def test_deferred_until_is_persisted_in_the_future(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _always_defers(monkeypatch, retry_after_s=120.0)
    deps = _build_deps(tmp_path, _DispatchTransport({}))
    store = Store(tmp_path / "queue_data")
    queue = JobQueue(store)
    job_id = _enqueue_analyze(queue)

    before = now_utc_naive()
    claimed = queue.claim_next(BUDGET)
    assert claimed is not None
    run_claimed_job(queue, claimed, deps)

    job = queue.get(job_id)
    assert job is not None
    assert job.deferred_until is not None
    assert job.deferred_until > before
    assert job.finished_at is None  # adiado, nunca terminado
    store.close()


def test_elapsed_deferral_remains_claimable_below_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B2 preservado: uma vez `deferred_until` vencido, o MESMO job volta a
    ser elegível sem precisar de outro job `queued` coexistindo.
    """
    _always_defers(monkeypatch)
    deps = _build_deps(tmp_path, _DispatchTransport({}), cold_build_defer_retry_s=0.0)
    store = Store(tmp_path / "queue_data")
    queue = JobQueue(store)
    job_id = _enqueue_analyze(queue)

    first = queue.claim_next(BUDGET)
    assert first is not None
    run_claimed_job(queue, first, deps)

    resumed = queue.claim_next(BUDGET)
    assert resumed is not None
    assert resumed.job_id == job_id
    assert resumed.defer_count == 1
    store.close()


def test_exactly_at_threshold_becomes_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _always_defers(monkeypatch)
    deps = _build_deps(tmp_path, _DispatchTransport({}), cold_build_defer_retry_s=0.0)
    store = Store(tmp_path / "queue_data")
    queue = JobQueue(store)
    job_id = _enqueue_analyze(queue)

    for _ in range(MAX_COLD_COHORT_DEFERS - 1):
        claimed = queue.claim_next(BUDGET)
        assert claimed is not None
        outcome = run_claimed_job(queue, claimed, deps)
        assert outcome.deferred is True

    claimed = queue.claim_next(BUDGET)
    assert claimed is not None
    assert claimed.defer_count == MAX_COLD_COHORT_DEFERS - 1
    outcome = run_claimed_job(queue, claimed, deps)

    assert outcome.deferred is False
    assert outcome.ok is False
    assert "excedeu" in outcome.message
    assert str(MAX_COLD_COHORT_DEFERS) in outcome.message

    job = queue.get(job_id)
    assert job is not None
    assert job.status == "failed"
    assert job.defer_count == MAX_COLD_COHORT_DEFERS - 1  # nunca incrementado uma 3a vez
    assert job.finished_at is not None
    assert job.error is not None
    store.close()


def test_above_threshold_never_defers_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _always_defers(monkeypatch)
    deps = _build_deps(tmp_path, _DispatchTransport({}), cold_build_defer_retry_s=0.0)
    store = Store(tmp_path / "queue_data")
    queue = JobQueue(store)
    _enqueue_analyze(queue)

    for _ in range(MAX_COLD_COHORT_DEFERS):
        claimed = queue.claim_next(BUDGET)
        assert claimed is not None
        run_claimed_job(queue, claimed, deps)

    # Nenhum novo wakeup reclama o job terminal — nunca mais.
    assert queue.claim_next(BUDGET) is None
    assert queue.claim_next(BUDGET) is None
    store.close()


def test_restart_simulation_respects_the_persisted_defer_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O contador que decide o teto vive no banco (`jobs.defer_count`), não
    em memória do processo worker — reabrir Store/JobQueue no MESMO path é
    o "restart do worker" que CL.0 precisa sobreviver.
    """
    _always_defers(monkeypatch)
    deps = _build_deps(tmp_path, _DispatchTransport({}), cold_build_defer_retry_s=0.0)
    db_path = tmp_path / "queue_data"

    store1 = Store(db_path)
    queue1 = JobQueue(store1)
    job_id = _enqueue_analyze(queue1)
    claimed = queue1.claim_next(BUDGET)
    assert claimed is not None
    run_claimed_job(queue1, claimed, deps)
    assert queue1.get(job_id).defer_count == 1  # type: ignore[union-attr]
    store1.close()

    # "Restart": processo novo, Store/JobQueue novos, MESMO arquivo.
    store2 = Store(db_path)
    queue2 = JobQueue(store2)
    resumed = queue2.claim_next(BUDGET)
    assert resumed is not None
    assert resumed.job_id == job_id
    assert resumed.defer_count == 1  # sobreviveu ao "restart"
    run_claimed_job(queue2, resumed, deps)
    assert queue2.get(job_id).defer_count == 2  # type: ignore[union-attr]

    # Um terceiro "restart" leva o job ao teto.
    store2.close()
    store3 = Store(db_path)
    queue3 = JobQueue(store3)
    final_claim = queue3.claim_next(BUDGET)
    assert final_claim is not None
    assert final_claim.defer_count == 2
    outcome = run_claimed_job(queue3, final_claim, deps)
    assert outcome.deferred is False

    job = queue3.get(job_id)
    assert job is not None
    assert job.status == "failed"
    assert job.defer_count == 2  # o teto persistiu por dois "restarts"
    store3.close()


def test_budget_only_defer_still_defers_normally_below_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uma indisponibilidade temporária de orçamento — sem NO_PROGRESS
    nenhum, só o preflight recusando por falta de pontos — continua sendo
    tratada como `deferred_budget` enquanto abaixo do teto. O modelo atual
    (`CohortDeferredBudget` sem um campo `defer_reason` estrutural — ver a
    docstring de `_defer_job`) não distingue essa causa de NO_PROGRESS na
    mensagem; este teste prova que a política conservadora (mesmo teto
    para as duas) NÃO transforma esta escassez transitória em falha
    prematura antes do limite.
    """
    _always_defers(monkeypatch, planned=None, completed=None)  # sem progresso: só preflight
    deps = _build_deps(tmp_path, _DispatchTransport({}), cold_build_defer_retry_s=0.0)
    store = Store(tmp_path / "queue_data")
    queue = JobQueue(store)
    job_id = _enqueue_analyze(queue)

    claimed = queue.claim_next(BUDGET)
    assert claimed is not None
    outcome = run_claimed_job(queue, claimed, deps)

    assert outcome.deferred is True
    job = queue.get(job_id)
    assert job is not None
    assert job.status == "deferred_budget"
    store.close()


def test_benchmark_build_defer_cap_is_independent_and_unaffected() -> None:
    """CL.0 não copia o valor do benchmark, e não deve — as duas
    semânticas divergem (ver docstring de `MAX_COLD_COHORT_DEFERS`).
    """
    assert MAX_COLD_COHORT_DEFERS != _MAX_CONSECUTIVE_DEFERS
    assert MAX_COLD_COHORT_DEFERS < _MAX_CONSECUTIVE_DEFERS
    assert _MAX_CONSECUTIVE_DEFERS == 50  # não tocado por CL.0


def test_normal_analyze_hot_path_is_unaffected(tmp_path: Path) -> None:
    from test_pipeline import _happy_path_responses

    transport = _DispatchTransport(_happy_path_responses())
    deps = _build_deps(tmp_path, transport)
    store = Store(tmp_path / "queue_data")
    queue = JobQueue(store)
    job_id = _enqueue_analyze(queue)

    claimed = queue.claim_next(BUDGET)
    assert claimed is not None
    outcome = run_claimed_job(queue, claimed, deps)

    assert outcome.ok is True
    assert outcome.deferred is False
    job = queue.get(job_id)
    assert job is not None
    assert job.status == "done"
    assert job.defer_count == 0  # nunca deferiu — teto nunca foi consultado
    store.close()


# ==================================================================================
# GRUPO B — regressão principal: reprodução real do incidente
# ==================================================================================

PRIMARY_REPORT = "ABCDEFGHIJKLMNOP"
PRIMARY_FIGHT = 1
PRIMARY_PLAYER = "Zarad"
DURATION_S = 100.0
N_GOOD_REFS = 8  # COHORT_MIN_HARD


class _IdempotentTransport(httpx.BaseTransport):
    """Como `_DispatchTransport` (test_pipeline.py), mas correlaciona
    `GetPlayerMeta` pelo `report_code` da própria requisição em vez de
    consumir uma fila — permite reivindicar o MESMO job repetidas vezes
    (várias janelas/claims) sem esgotar respostas, exatamente como a WCL
    real responderia identicamente à mesma pergunta em cada retomada.

    `unfetchable`: os "Anonymous" — sempre devolve nenhum jogador
    encontrado, para qualquer report_code neste conjunto, para sempre.
    """

    def __init__(
        self, *, good: dict[str, str], unfetchable: set[str], rankings: dict[str, Any]
    ) -> None:
        self._good = good
        self._unfetchable = unfetchable
        self._rankings = rankings
        self.calls: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        from test_log_fetcher import (
            _events_response,
            _meta_response,
            _percentile_response,
            _report_rankings_response,
        )
        from test_pipeline import _zone_partitions_response

        url = str(request.url)
        if "oauth.battle.net" in url or "warcraftlogs.com/oauth" in url:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        body = json.loads(request.content)
        query = body.get("query", "")
        variables = body.get("variables") or {}
        if "rateLimitData" in query:
            # pointsResetIn=0 (nao o padrao fixo de 3600 de
            # _rate_limit_response): o teste controla o retry via
            # cold_build_defer_retry_s=0.0, e um pointsResetIn real de 3600
            # sobrescreveria isso (retry_after_s viria do cliente, nao do
            # fallback), deixando deferred_until 1h no futuro e cada
            # reclamacao subsequente do teste falharia.
            return httpx.Response(
                200,
                json={
                    "data": {
                        "rateLimitData": {
                            "limitPerHour": 10000,
                            "pointsSpentThisHour": 0.0,
                            "pointsResetIn": 0,
                        }
                    }
                },
            )
        if "GetPlayerMeta" in query:
            self.calls.append("meta")
            code = str(variables.get("code"))
            if code in self._unfetchable:
                return httpx.Response(200, json=_meta_response(no_player=True))
            name = self._good.get(code, "Unknown")
            return httpx.Response(
                200, json=_meta_response(player_name=name, damage_total=900_000.0)
            )
        if "GetPlayerDamageEvents" in query:
            self.calls.append("damage_events")
            return httpx.Response(200, json=_events_response([]))
        if "GetPlayerResourceEvents" in query:
            self.calls.append("resource_events")
            return httpx.Response(200, json=_events_response([]))
        if "GetPlayerEvents" in query:
            self.calls.append("events")
            return httpx.Response(200, json=_events_response([]))
        if "GetPercentile" in query:
            self.calls.append("percentile")
            return httpx.Response(200, json=_percentile_response(None, "x", 1))
        if "GetReportRankings" in query:
            self.calls.append("report_rankings")
            return httpx.Response(200, json=_report_rankings_response(no_data=True))
        if "GetRankingsCDs" in query:
            self.calls.append("rankings")
            return httpx.Response(200, json=self._rankings)
        if "GetZonePartitions" in query:
            self.calls.append("partition")
            return httpx.Response(200, json=_zone_partitions_response())
        if "GetPlayerDebuffs" in query:
            self.calls.append("debuffs")
            return httpx.Response(200, json=_buffs_response())
        if "GetPlayerBuffs" in query:
            self.calls.append("buffs")
            return httpx.Response(200, json=_buffs_response())
        pytest.fail(f"query GraphQL não reconhecida: {query[:80]}")


def _unreachable_scenario() -> tuple[dict[str, str], set[str], dict[str, Any]]:
    """8 referências fetcháveis + 2 permanentemente inatingíveis
    ("Anonymous") — o MESMO formato do job real `2b3a1091d00c...`: quase
    completo, bloqueado por stragglers que a WCL nunca vai servir.
    """
    from test_cohort_builder import _ranking, _rankings_page

    good = {PRIMARY_REPORT: PRIMARY_PLAYER}
    good.update({f"REFOK{i:011d}": f"Ref{i}" for i in range(N_GOOD_REFS)})
    unfetchable = {f"ANON{i:012d}" for i in range(2)}

    entries = [
        _ranking(name, DURATION_S, code) for code, name in good.items() if code != PRIMARY_REPORT
    ]
    entries += [
        _ranking(f"Anon{i}", DURATION_S, code) for i, code in enumerate(sorted(unfetchable))
    ]
    rankings = _rankings_page(entries)
    return good, unfetchable, rankings


def _regression_env(tmp_path: Path) -> tuple[Any, JobQueue, Store, _IdempotentTransport]:
    good, unfetchable, rankings = _unreachable_scenario()
    transport = _IdempotentTransport(good=good, unfetchable=unfetchable, rankings=rankings)
    deps = _build_deps(tmp_path, transport, cold_build_defer_retry_s=0.0)
    store = Store(tmp_path / "queue_data")
    queue = JobQueue(store)
    return deps, queue, store, transport


def test_regression_permanently_unfetchable_candidates_eventually_terminate(
    tmp_path: Path,
) -> None:
    """A reprodução principal exigida: `run_claimed_job` de verdade, sem
    mockar `run_analysis`/`advance_cohort_build` — os mesmos 2 candidatos
    "Anonymous" reais bloqueiam a MESMA coorte a cada retomada, e o worker
    a reclama repetidamente como faria após vários wakeups reais.

    defer -> wake -> defer -> ... -> terminal, depois: novo wakeup -> job
    NÃO é reclamado.
    """
    deps, queue, store, _transport = _regression_env(tmp_path)
    job_id = _enqueue_analyze(queue)

    outcomes = []
    for attempt in range(1, MAX_COLD_COHORT_DEFERS + 1):
        claimed = queue.claim_next(BUDGET)
        assert claimed is not None, f"job não reclamável na tentativa {attempt}"
        assert claimed.job_id == job_id  # 12: mesmo job_id em toda retomada
        assert claimed.dedup_key == ANALYZE_DEDUP_KEY  # 13: dedup nunca muda
        outcomes.append(run_claimed_job(queue, claimed, deps))

    # As MAX_COLD_COHORT_DEFERS-1 primeiras tentativas: deferred_budget,
    # sempre por NO_PROGRESS real (não simulado).
    for outcome in outcomes[:-1]:
        assert outcome.deferred is True
        assert outcome.ok is False

    # A última: terminal, NUNCA deferida de novo.
    assert outcomes[-1].deferred is False
    assert outcomes[-1].ok is False

    final = queue.get(job_id)
    assert final is not None
    assert final.status == "failed"  # 11: terminal
    assert final.defer_count == MAX_COLD_COHORT_DEFERS - 1
    assert final.dedup_key == ANALYZE_DEDUP_KEY

    # Novo wakeup do worker: o job terminal NÃO é reclamado — prova de
    # não-reclaim, e portanto de que nenhuma nova query WCL pode nascer
    # dele (claim_next é o ÚNICO ponto que injeta um job em run_claimed_job;
    # sem reclamação, `_advance_cold_cohort`/`fetch_ranking_candidates`
    # nunca roda de novo para este job).
    assert queue.claim_next(BUDGET) is None
    assert queue.claim_next(BUDGET) is None
    store.close()


def test_regression_no_new_wcl_query_possible_after_termination(tmp_path: Path) -> None:
    """Prova offline de que, após o estado terminal, nenhuma nova query WCL
    PODERIA ser disparada por este job — sem fazer nenhuma query real: o
    contador de chamadas do próprio transporte fake fica estático depois
    do último `claim_next` retornar `None`.
    """
    deps, queue, store, transport = _regression_env(tmp_path)
    _enqueue_analyze(queue)

    for _ in range(MAX_COLD_COHORT_DEFERS):
        claimed = queue.claim_next(BUDGET)
        assert claimed is not None
        run_claimed_job(queue, claimed, deps)

    calls_at_termination = list(transport.calls)
    assert queue.claim_next(BUDGET) is None
    assert queue.claim_next(BUDGET) is None
    # Nenhuma chamada nova aconteceu — não havia como, pois nada mais
    # chamou run_claimed_job com este job (claim_next nunca o devolveu).
    assert transport.calls == calls_at_termination
    store.close()


def test_regression_good_candidates_are_cached_and_never_refetched(tmp_path: Path) -> None:
    """Os 8 candidatos bons só são buscados na PRIMEIRA janela — nas
    janelas seguintes já estão em cache (`Store.read_log`), então cada
    retomada só reconsulta os 2 permanentemente inatingíveis. Prova que a
    política não reintroduz o custo de refetch que o incidente real
    mostrou para candidatos JÁ resolvidos — só os genuinamente pendentes
    são retentados.
    """
    deps, queue, store, transport = _regression_env(tmp_path)
    _enqueue_analyze(queue)

    claimed = queue.claim_next(BUDGET)
    assert claimed is not None
    run_claimed_job(queue, claimed, deps)
    meta_calls_after_first_window = transport.calls.count("meta")
    assert meta_calls_after_first_window >= N_GOOD_REFS + 2 + 1  # refs + anon + o proprio jogador

    claimed = queue.claim_next(BUDGET)
    assert claimed is not None
    run_claimed_job(queue, claimed, deps)
    meta_calls_after_second_window = transport.calls.count("meta") - meta_calls_after_first_window
    # Só os 2 Anonymous (nunca cacheados) sao retentados na segunda janela.
    assert meta_calls_after_second_window == 2
    store.close()
