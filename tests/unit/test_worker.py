"""T1.8 — tests for bot/worker.py's run_claimed_job. Reuses test_pipeline.py's
dispatch transport/response builders and test_cohort_builder.py's ranking
response helpers (same fake-httpx-transport pattern throughout this
session's test suite).
"""

from __future__ import annotations

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


def test_budget_defer_is_honest_terminal_job_and_worker_can_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    assert not outcome.ok and not outcome.requeued
    assert "temporariamente reservado" in outcome.message
    assert queue.get(claimed.job_id).status == "failed"  # type: ignore[union-attr]
    assert queue.list_active() == []
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
