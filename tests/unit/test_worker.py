"""T1.8 — tests for bot/worker.py's run_claimed_job. Reuses test_pipeline.py's
dispatch transport/response builders and test_cohort_builder.py's ranking
response helpers (same fake-httpx-transport pattern throughout this
session's test suite).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_cohort_builder import _responses_for
from test_log_fetcher import _meta_response
from test_pipeline import _build_deps, _DispatchTransport, _happy_path_responses

import botgitgud.bot.worker as worker_module
from botgitgud.bot.job_models import BudgetStatus, Job, now_utc_naive
from botgitgud.bot.jobs import JobQueue
from botgitgud.bot.worker import run_claimed_job
from botgitgud.errors import CohortDeferredBudget, RateLimitBudgetExceeded
from botgitgud.ingest.store import Store
from botgitgud.report.html_report import render_html_report
from botgitgud.report.text import render_header_and_top3

ENCOUNTER_ID = 3179


def _claimed_analyze_job(dedup_key: str = "ABCDEFGHIJKLMNOP:1:Zarad") -> Job:
    return Job(
        job_id="job-1",
        job_type="analyze",
        dedup_key=dedup_key,
        discord_user_id="user-1",
        discord_channel_id="chan-1",
        status="running",
        created_at=now_utc_naive(),
        started_at=now_utc_naive(),
        finished_at=None,
        error=None,
        report_path=None,
    )


def _claimed_build_cohort_job(dedup_key: str) -> Job:
    return Job(
        job_id="job-2",
        job_type="build_cohort",
        dedup_key=dedup_key,
        discord_user_id="user-1",
        discord_channel_id="chan-1",
        status="running",
        created_at=now_utc_naive(),
        started_at=now_utc_naive(),
        finished_at=None,
        error=None,
        report_path=None,
    )


def test_run_claimed_analyze_job_returns_summary_and_html(tmp_path: Path) -> None:
    transport = _DispatchTransport(_happy_path_responses())
    deps = _build_deps(tmp_path, transport)
    store = Store(tmp_path / "queue_data")
    queue = JobQueue(store)
    job = _claimed_analyze_job()

    outcome = run_claimed_job(queue, job, deps)

    assert outcome.ok is True
    assert "Zarad" in outcome.message
    assert "TOP 3 AÇÕES COM GANHO" in outcome.message
    assert outcome.html_report is not None
    assert "<html" in outcome.html_report
    assert "DE ONDE VEIO O GAP DE DPS" not in outcome.message
    store.close()


def test_queue_and_hot_path_render_the_same_delivery_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps = _build_deps(tmp_path, _DispatchTransport(_happy_path_responses()))
    result = worker_module.run_analysis(
        worker_module.AnalysisRequest("ABCDEFGHIJKLMNOP", 1, "Zarad"),
        deps,
        allow_cold_build=True,
    )
    monkeypatch.setattr(worker_module, "run_analysis", lambda *_args, **_kwargs: result)
    summary, html = worker_module._run_analyze(_claimed_analyze_job(), deps)
    assert summary == render_header_and_top3(result.header, result.top_actions)
    assert html == render_html_report(
        result.header,
        result.comparisons,
        manifest=result.manifest,
        build_divergence=result.build_divergence,
        performance=result.performance,
        dps_gap=result.dps_gap,
        top_actions=result.top_actions,
        duration_s=result.header.duration_max_s,
    )


def test_run_claimed_analyze_job_marks_queue_entry_done_when_enqueued_first(
    tmp_path: Path,
) -> None:
    transport = _DispatchTransport(_happy_path_responses())
    deps = _build_deps(tmp_path, transport)
    store = Store(tmp_path / "queue_data")
    queue = JobQueue(store)
    enqueued = queue.enqueue(
        job_type="analyze",
        dedup_key="ABCDEFGHIJKLMNOP:1:Zarad",
        discord_user_id="user-1",
        discord_channel_id="chan-1",
    )
    assert enqueued.job is not None
    claimed = queue.claim_next(BudgetStatus(points_remaining=3600.0, limit_per_hour=3600.0))
    assert claimed is not None

    outcome = run_claimed_job(queue, claimed, deps)

    assert outcome.ok is True
    done = queue.get(claimed.job_id)
    assert done is not None
    assert done.status == "done"
    store.close()


def test_run_claimed_analyze_job_marks_failed_on_error(tmp_path: Path) -> None:
    responses: dict[str, Any] = {
        "meta": [_meta_response(no_player=True)],
        "events": [],
        "percentile": [],
    }
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)
    store = Store(tmp_path / "queue_data")
    queue = JobQueue(store)
    enqueued = queue.enqueue(
        job_type="analyze",
        dedup_key="ABCDEFGHIJKLMNOP:1:Nobody",
        discord_user_id="user-1",
        discord_channel_id="chan-1",
    )
    assert enqueued.job is not None
    claimed = queue.claim_next(BudgetStatus(points_remaining=3600.0, limit_per_hour=3600.0))
    assert claimed is not None

    outcome = run_claimed_job(queue, claimed, deps)

    assert outcome.ok is False
    failed = queue.get(claimed.job_id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error is not None
    store.close()


def test_run_claimed_build_cohort_job_returns_a_summary(tmp_path: Path) -> None:
    responses = _responses_for({100.0: 8})
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)
    store = Store(tmp_path / "queue_data")
    queue = JobQueue(store)
    job = _claimed_build_cohort_job("cohort:3179:Warlock:Demonology:5:all")

    outcome = run_claimed_job(queue, job, deps)

    assert outcome.ok is True
    assert "1 coorte" in outcome.message
    store.close()


def test_budget_defer_is_not_a_failure_and_keeps_the_job_eligible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B2 — a regressao central desta correcao.

    Ate a v1.0-rc, `CohortDeferredBudget` caia no ramo generico
    `except BotGitGudError` e o job era marcado `failed`: o pedido do usuario
    era descartado logo depois de o bot prometer que ele continuaria, e o
    progresso ja pago em pontos de API ia junto.
    """
    deps = _build_deps(tmp_path, _DispatchTransport(_responses_for({100.0: 8})))
    store = Store(tmp_path / "queue_data")
    queue = JobQueue(store)
    enqueued = queue.enqueue(
        job_type="build_cohort",
        dedup_key="cohort:3179:Warlock:Demonology:5:100.0",
        discord_user_id="user-1",
        discord_channel_id="chan-1",
    )
    assert enqueued.job is not None
    claimed = queue.claim_next(BudgetStatus(points_remaining=9000, limit_per_hour=10000))
    assert claimed is not None
    monkeypatch.setattr(
        worker_module,
        "build_cohorts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            CohortDeferredBudget(
                "orçamento temporariamente reservado",
                cohort_id="abc",
                estimated_api_points=3000,
                available_api_points=3500,
                protected_floor=1000,
                safety_margin=250,
            )
        ),
    )

    outcome = run_claimed_job(queue, claimed, deps)

    assert not outcome.ok
    assert outcome.deferred is True
    assert "temporariamente reservado" in outcome.message

    deferred = queue.get(claimed.job_id)
    assert deferred is not None
    assert deferred.status == "deferred_budget"  # nao "failed"
    assert deferred.finished_at is None  # o trabalho nao terminou
    assert deferred.dedup_key == "cohort:3179:Warlock:Demonology:5:100.0"
    assert deferred.defer_count == 1
    assert deferred.deferred_until is not None
    # Continua visivel como trabalho ativo — some da fila seria mentir.
    assert [j.job_id for j in queue.list_active()] == [claimed.job_id]
    store.close()


def test_run_claimed_build_cohort_job_with_explicit_bucket(tmp_path: Path) -> None:
    responses = _responses_for({100.0: 8, 500.0: 8})
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)
    store = Store(tmp_path / "queue_data")
    queue = JobQueue(store)
    job = _claimed_build_cohort_job("cohort:3179:Warlock:Demonology:5:500.0")

    outcome = run_claimed_job(queue, job, deps)

    assert outcome.ok is True
    assert "1 coorte" in outcome.message
    store.close()


# -- T1.8 §3: RateLimitBudgetExceeded mid-job requeues, never marks failed ------


def test_analyze_job_requeued_not_failed_on_rate_limit_budget_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*_args: object, **_kwargs: object) -> None:
        raise RateLimitBudgetExceeded(
            "orçamento excedido", points_remaining=10.0, reset_in_seconds=60.0
        )

    monkeypatch.setattr(worker_module, "run_analysis", _raise)
    deps = _build_deps(tmp_path, _DispatchTransport({}))
    store = Store(tmp_path / "queue_data")
    queue = JobQueue(store)
    enqueued = queue.enqueue(
        job_type="analyze",
        dedup_key="ABCDEFGHIJKLMNOP:1:Zarad",
        discord_user_id="user-1",
        discord_channel_id="chan-1",
    )
    assert enqueued.job is not None
    claimed = queue.claim_next(BudgetStatus(points_remaining=3600.0, limit_per_hour=3600.0))
    assert claimed is not None

    outcome = run_claimed_job(queue, claimed, deps)

    assert outcome.requeued is True
    assert outcome.ok is False
    requeued_job = queue.get(claimed.job_id)
    assert requeued_job is not None
    assert requeued_job.status == "queued"  # not "failed"
    assert requeued_job.started_at is None
    store.close()


def test_build_cohort_job_requeued_not_failed_on_rate_limit_budget_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*_args: object, **_kwargs: object) -> None:
        raise RateLimitBudgetExceeded(
            "orçamento excedido", points_remaining=10.0, reset_in_seconds=60.0
        )

    monkeypatch.setattr(worker_module, "build_cohorts", _raise)
    deps = _build_deps(tmp_path, _DispatchTransport({}))
    store = Store(tmp_path / "queue_data")
    queue = JobQueue(store)
    job = _claimed_build_cohort_job("cohort:3179:Warlock:Demonology:5:all")

    outcome = run_claimed_job(queue, job, deps)

    assert outcome.requeued is True
    assert outcome.ok is False
    store.close()


# -- B2: adiamento e retomada do MESMO job -------------------------------------


def _deferred_error(planned: int = 100, completed: int = 30) -> CohortDeferredBudget:
    return CohortDeferredBudget(
        "orçamento temporariamente reservado",
        cohort_id="cohort-x",
        estimated_api_points=3000,
        available_api_points=1200,
        protected_floor=1000,
        safety_margin=250,
        planned=planned,
        completed=completed,
    )


def test_a_deferred_job_resumes_as_the_same_job_and_finally_delivers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O requisito de produto: o usuário NÃO precisa reenviar `!analisar`.

    O mesmo job — mesma dedup_key, mesmo job_id — volta sozinho na janela
    seguinte e entrega o relatório.
    """
    transport = _DispatchTransport(_happy_path_responses())
    # retry 0: o teste não espera uma janela horária real para provar a
    # retomada; a política de espera em si é testada em test_jobs.py.
    deps = _build_deps(tmp_path, transport, cold_build_defer_retry_s=0.0)
    store = Store(tmp_path / "queue_data")
    queue = JobQueue(store)
    enqueued = queue.enqueue(
        job_type="analyze",
        dedup_key="ABCDEFGHIJKLMNOP:1:Zarad",
        discord_user_id="user-1",
        discord_channel_id="chan-1",
    )
    assert enqueued.job is not None
    original_job_id = enqueued.job.job_id
    budget = BudgetStatus(points_remaining=3600.0, limit_per_hour=3600.0)

    calls = {"n": 0}
    real_run_analysis = worker_module.run_analysis

    def _flaky(*args: object, **kwargs: object) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            raise _deferred_error()
        return real_run_analysis(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(worker_module, "run_analysis", _flaky)

    first = queue.claim_next(budget)
    assert first is not None
    deferred_outcome = run_claimed_job(queue, first, deps)
    assert deferred_outcome.deferred is True

    # Repetir o pedido enquanto adiado NAO cria um segundo job.
    again = queue.enqueue(
        job_type="analyze",
        dedup_key="ABCDEFGHIJKLMNOP:1:Zarad",
        discord_user_id="user-2",
        discord_channel_id="chan-1",
    )
    assert again.deduped is True
    assert again.job is not None
    assert again.job.job_id == original_job_id

    # Janela seguinte: o MESMO job e reclamado sozinho.
    resumed = queue.claim_next(budget)
    assert resumed is not None
    assert resumed.job_id == original_job_id
    assert resumed.dedup_key == "ABCDEFGHIJKLMNOP:1:Zarad"
    assert resumed.defer_count == 1  # a contagem de adiamentos sobrevive

    final_outcome = run_claimed_job(queue, resumed, deps)

    assert final_outcome.ok is True
    assert final_outcome.html_report is not None
    assert final_outcome.deferred is False
    done = queue.get(original_job_id)
    assert done is not None
    assert done.status == "done"
    assert done.report_path is not None
    store.close()


def test_deferred_analysis_leaves_auditable_telemetry_without_claiming_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Um adiamento precisa provar QUANTO avançou — e não pode se registrar
    como análise concluída nem como falha de análise.
    """
    deps = _build_deps(tmp_path, _DispatchTransport({}), cold_build_defer_retry_s=0.0)
    store = Store(tmp_path / "queue_data")
    queue = JobQueue(store)
    monkeypatch.setattr(
        worker_module,
        "run_analysis",
        lambda *_a, **_k: (_ for _ in ()).throw(_deferred_error(planned=100, completed=30)),
    )
    job = _claimed_analyze_job()

    run_claimed_job(queue, job, deps)

    runs_dir = deps.settings.data_dir / "ops" / "analysis-runs"
    payloads = [json.loads(p.read_text(encoding="utf-8")) for p in runs_dir.glob("*.json")]
    assert len(payloads) == 1
    run = payloads[0]
    assert run["final_status"] == "deferred_budget"
    assert run["cold_build_started"] is True
    assert run["cold_build_state"] == "deferred_budget"
    assert run["cold_build_planned"] == 100
    assert run["cold_build_completed"] == 30
    assert run["cold_build_remaining"] == 70
    assert run["cohort_id"] == "cohort-x"
    assert run["job_id"] == job.job_id
    store.close()


def test_a_successful_cold_build_is_not_reported_as_hot_path(tmp_path: Path) -> None:
    """Os dois caminhos produzem o mesmo relatório; só o custo difere. A
    telemetria não pode inferir qual foi — precisa do fato vindo do pipeline.
    """
    deps = _build_deps(tmp_path, _DispatchTransport(_happy_path_responses()))
    store = Store(tmp_path / "queue_data")
    queue = JobQueue(store)

    outcome = run_claimed_job(queue, _claimed_analyze_job(), deps)
    assert outcome.ok is True

    runs_dir = deps.settings.data_dir / "ops" / "analysis-runs"
    run = json.loads(next(iter(runs_dir.glob("*.json"))).read_text(encoding="utf-8"))
    assert run["cold_build_started"] is True
    assert run["hot_path"] is False
    assert run["cold_build_state"] == "ready"
    assert run["final_status"] == "completed"
    store.close()
