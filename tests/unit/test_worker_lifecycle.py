"""RC.4-RC.8/RC.11/RC.14 - lifecycle do worker e sobrevivencia a falha de entrega.

Regressao do incidente real: a analise do tipo Zilbag conclui, o envio do HTML
recebe 403/50013, e antes desta correcao a excecao matava a task do worker.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from botgitgud.bot.delivery import ReportDeliveryConfig
from botgitgud.bot.discord_bot import WorkerSupervisor, _notify_outcome, _run_one_job
from botgitgud.bot.job_models import BudgetStatus
from botgitgud.bot.jobs import JobQueue
from botgitgud.bot.report_links import ReportLinkStore
from botgitgud.bot.report_store import persist_report
from botgitgud.bot.worker import JobOutcome
from botgitgud.ingest.store import Store
from botgitgud.report.contract import ConfidenceSummary, ExecutionSection, ReportContract
from botgitgud.report.text import ReportHeader


def _forbidden(code: int = 50013) -> discord.Forbidden:
    response: Any = type("R", (), {"status": 403, "reason": "Forbidden"})()
    return discord.Forbidden(response, {"code": code, "message": "Missing Permissions"})


def _http_error() -> discord.HTTPException:
    response: Any = type("R", (), {"status": 500, "reason": "Server Error"})()
    return discord.HTTPException(response, {"code": 0, "message": "boom"})


class _Channel:
    def __init__(self, *, fail: Exception | None = None) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self._fail = fail

    async def send(self, content: str, **kwargs: Any) -> None:
        if self._fail is not None:
            raise self._fail
        self.sent.append((content, kwargs))


def _contract() -> ReportContract:
    return ReportContract(
        resultado=ReportHeader("Zilbag", "Boss", "DeathKnight", "Unholy", 20, 300.0, 360.0),
        setup=None,
        execucao=ExecutionSection(comparisons=(), performance=None, dps_gap=None),
        top_actions=(),
        confianca=ConfidenceSummary(
            reference_pool_members=40,
            matched_cohort_members=20,
            cohort_warnings=(),
            matched_covariates=(),
            relaxed_covariates=(),
        ),
        manifest=None,
    )  # type: ignore[arg-type]


def _delivery_config(tmp_path: Path) -> ReportDeliveryConfig:
    return ReportDeliveryConfig(
        link_store=ReportLinkStore(Store(tmp_path)),
        data_dir=tmp_path,
        public_base_url="https://botgitgud.duckdns.org",
    )


class _Bot:
    def __init__(self, channel: _Channel) -> None:
        self._channel = channel

    def get_channel(self, _id: int) -> _Channel:
        return self._channel


def _claim_one(queue: JobQueue) -> Any:
    queue.enqueue(
        job_type="analyze",
        dedup_key="ABCDEFGHIJKLMNOP:1:Zilbag",
        discord_user_id="123",
        discord_channel_id="456",
    )
    return queue.claim_next(BudgetStatus(3600.0, 3600.0))


# -- RC.8: regressao exata do incidente ------------------------------------------


def test_zilbag_like_forbidden_preserves_report_and_records_delivery_failure(
    tmp_path: Path,
) -> None:
    with Store(tmp_path) as store:
        queue = JobQueue(store)
        job = _claim_one(queue)
        assert job is not None
        report = persist_report(tmp_path, job.job_id, "<html>Zilbag</html>")
        queue.mark_done(job.job_id, report_path=str(report))

        channel = _Channel(fail=_forbidden())
        outcome = JobOutcome(
            job,
            True,
            "resumo",
            html_report="<html>Zilbag</html>",
            report_path=str(report),
            report_contract=_contract(),
        )
        asyncio.run(_notify_outcome(_Bot(channel), outcome, queue, _delivery_config(tmp_path)))

        stored = queue.get(job.job_id)
        assert stored is not None
        assert stored.status == "done"
        assert stored.analysis_completed is True
        assert stored.delivery_status == "failed"
        assert stored.delivery_error is not None
        assert stored.report_path == str(report)
        assert report.is_file()


def test_successful_delivery_is_recorded(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        queue = JobQueue(store)
        job = _claim_one(queue)
        assert job is not None
        queue.mark_done(job.job_id, report_path=str(persist_report(tmp_path, job.job_id, "<h/>")))
        outcome = JobOutcome(job, True, "r", html_report="<h/>", report_contract=_contract())
        asyncio.run(_notify_outcome(_Bot(_Channel()), outcome, queue, _delivery_config(tmp_path)))
        stored = queue.get(job.job_id)
        assert stored is not None
        assert stored.delivery_status == "delivered"


def test_analysis_failure_stays_distinct_from_delivery_failure(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        queue = JobQueue(store)
        job = _claim_one(queue)
        assert job is not None
        queue.mark_failed(job.job_id, error="InsufficientCohort")
        outcome = JobOutcome(job, False, "sem coorte")
        asyncio.run(_notify_outcome(_Bot(_Channel()), outcome, queue, _delivery_config(tmp_path)))
        stored = queue.get(job.job_id)
        assert stored is not None
        assert stored.status == "failed"
        assert stored.error == "InsufficientCohort"
        assert stored.delivery_status == "delivered"


def test_http_exception_does_not_escape_notify(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        queue = JobQueue(store)
        job = _claim_one(queue)
        assert job is not None
        queue.mark_done(job.job_id, report_path=str(persist_report(tmp_path, job.job_id, "<h/>")))
        channel = _Channel(fail=_http_error())
        outcome = JobOutcome(job, True, "r", html_report="<h/>", report_contract=_contract())
        asyncio.run(_notify_outcome(_Bot(channel), outcome, queue, _delivery_config(tmp_path)))
        stored = queue.get(job.job_id)
        assert stored is not None
        assert stored.delivery_status == "failed"


# -- RC.4: um job nunca mata o consumidor ----------------------------------------


def test_unexpected_job_exception_is_logged_and_job_is_not_left_running(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        queue = JobQueue(store)
        job = _claim_one(queue)
        assert job is not None
        deps = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))

        async def invoke() -> None:
            loop = asyncio.get_running_loop()

            async def boom(*_a: object, **_k: object) -> None:
                raise RuntimeError("bug inesperado")

            loop.run_in_executor = boom  # type: ignore[method-assign]
            await _run_one_job(_Bot(_Channel()), deps, queue, job, _delivery_config(tmp_path))  # type: ignore[arg-type]

        asyncio.run(invoke())
        stored = queue.get(job.job_id)
        assert stored is not None
        assert stored.status == "failed"


def test_cancellation_is_never_swallowed(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        queue = JobQueue(store)
        job = _claim_one(queue)
        assert job is not None
        deps = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))

        async def invoke() -> None:
            loop = asyncio.get_running_loop()

            async def cancelled(*_a: object, **_k: object) -> None:
                raise asyncio.CancelledError

            loop.run_in_executor = cancelled  # type: ignore[method-assign]
            await _run_one_job(_Bot(_Channel()), deps, queue, job, _delivery_config(tmp_path))  # type: ignore[arg-type]

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(invoke())


# -- RC.5/RC.6/RC.7: liveness real ------------------------------------------------


def test_supervisor_starts_one_worker_and_reconnect_does_not_duplicate() -> None:
    async def scenario() -> None:
        supervisor = WorkerSupervisor()
        started = asyncio.Event()

        async def worker() -> None:
            started.set()
            await asyncio.sleep(3600)

        assert supervisor.ensure_running(worker) is True
        await started.wait()
        assert supervisor.is_alive is True
        assert supervisor.ensure_running(worker) is False
        supervisor._task.cancel()  # type: ignore[union-attr]

    asyncio.run(scenario())


def test_dead_worker_is_replaced_by_a_new_one() -> None:
    async def scenario() -> None:
        supervisor = WorkerSupervisor()
        runs = 0

        async def worker() -> None:
            nonlocal runs
            runs += 1

        assert supervisor.ensure_running(worker) is True
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert supervisor.is_alive is False
        assert supervisor.ensure_running(worker) is True
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert runs == 2

    asyncio.run(scenario())


def test_never_two_concurrent_workers() -> None:
    async def scenario() -> None:
        supervisor = WorkerSupervisor()
        live = 0
        peak = 0

        async def worker() -> None:
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            try:
                await asyncio.sleep(3600)
            finally:
                live -= 1

        for _ in range(5):
            supervisor.ensure_running(worker)
            await asyncio.sleep(0)
        assert peak == 1
        supervisor._task.cancel()  # type: ignore[union-attr]

    asyncio.run(scenario())


def test_crashed_worker_drops_liveness() -> None:
    async def scenario() -> None:
        supervisor = WorkerSupervisor()

        async def worker() -> None:
            raise RuntimeError("loop quebrou")

        supervisor.ensure_running(worker)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert supervisor.is_alive is False

    asyncio.run(scenario())
