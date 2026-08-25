"""RC.11/RC.12 - invariantes entre analise concluida e artefato persistido,
e RC.14 - o !status precisa refletir liveness real do worker.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import botgitgud.bot.worker as worker_module
from botgitgud.bot.job_models import BudgetStatus
from botgitgud.bot.jobs import JobQueue
from botgitgud.bot.report_store import ReportPersistenceError, report_path_for
from botgitgud.bot.worker import run_claimed_job
from botgitgud.ingest.store import Store


def _claim(queue: JobQueue, dedup: str = "ABCDEFGHIJKLMNOP:1:Zilbag", user: str = "123") -> Any:
    # user distinto por job: MAX_ACTIVE_JOBS_PER_USER=1 e USER_COOLDOWN_S=60
    # (fairness da T1.8) recusariam um segundo enqueue do mesmo usuario.
    queue.enqueue(
        job_type="analyze", dedup_key=dedup, discord_user_id=user, discord_channel_id="456"
    )
    return queue.claim_next(BudgetStatus(3600.0, 3600.0))


def _deps(tmp_path: Path) -> Any:
    return SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))


# -- RC.11: analise concluida implica artefato em disco --------------------------


def test_completed_analysis_always_has_a_persisted_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        worker_module, "_run_analyze", lambda *_a: ("resumo", "<html>relatorio</html>")
    )
    with Store(tmp_path) as store:
        queue = JobQueue(store)
        job = _claim(queue)
        assert job is not None
        outcome = run_claimed_job(queue, job, _deps(tmp_path))

        stored = queue.get(job.job_id)
        assert stored is not None
        assert stored.status == "done"
        assert stored.report_path is not None
        assert Path(stored.report_path).is_file()
        assert Path(stored.report_path).read_text(encoding="utf-8") == "<html>relatorio</html>"
        assert outcome.report_path == stored.report_path


def test_report_is_persisted_before_any_delivery_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O outcome so existe depois que o arquivo esta no disco: quem entrega
    nunca recebe um artefato que ainda nao foi gravado.
    """
    monkeypatch.setattr(worker_module, "_run_analyze", lambda *_a: ("resumo", "<html/>"))
    with Store(tmp_path) as store:
        queue = JobQueue(store)
        job = _claim(queue)
        assert job is not None
        expected = report_path_for(tmp_path, job.job_id)
        assert not expected.exists()
        outcome = run_claimed_job(queue, job, _deps(tmp_path))
        assert outcome.ok is True
        assert expected.is_file()


# -- RC.12: falha de filesystem e erro de artifact, nao de entrega ---------------


def test_report_write_failure_fails_the_job_and_never_offers_a_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker_module, "_run_analyze", lambda *_a: ("resumo", "<html/>"))

    def boom(*_a: object, **_k: object) -> None:
        raise ReportPersistenceError("disco cheio")

    monkeypatch.setattr(worker_module, "persist_report", boom)
    with Store(tmp_path) as store:
        queue = JobQueue(store)
        job = _claim(queue)
        assert job is not None
        outcome = run_claimed_job(queue, job, _deps(tmp_path))

        assert outcome.ok is False
        assert outcome.html_report is None  # nada para anexar
        assert outcome.report_path is None
        stored = queue.get(job.job_id)
        assert stored is not None
        assert stored.status == "failed"
        assert stored.report_path is None
        assert "disco cheio" in (stored.error or "")


def test_next_job_still_runs_after_a_report_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker_module, "_run_analyze", lambda *_a: ("resumo", "<html/>"))
    calls = {"n": 0}
    real = worker_module.persist_report

    def fail_first(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ReportPersistenceError("falha transitoria")
        return real(*args, **kwargs)

    monkeypatch.setattr(worker_module, "persist_report", fail_first)
    with Store(tmp_path) as store:
        queue = JobQueue(store)
        first = _claim(queue, "AAAAAAAAAAAAAAAA:1:A", user="user-a")
        assert first is not None
        assert run_claimed_job(queue, first, _deps(tmp_path)).ok is False

        second = _claim(queue, "BBBBBBBBBBBBBBBB:2:B", user="user-b")
        assert second is not None
        outcome = run_claimed_job(queue, second, _deps(tmp_path))
        assert outcome.ok is True
        assert outcome.report_path is not None


# -- RC.14: !status nao pode mentir ----------------------------------------------


def test_status_reports_worker_running_and_stopped(tmp_path: Path) -> None:
    import botgitgud.bot.discord_bot as discord_module

    class _Ctx:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, content: str, *, file: Any | None = None) -> None:
            self.sent.append(content)

    async def scenario() -> None:
        with Store(tmp_path) as store:
            deps = SimpleNamespace(
                store=store, client=SimpleNamespace(), settings=SimpleNamespace(data_dir=tmp_path)
            )
            bot = discord_module.build_bot(deps)  # type: ignore[arg-type]
            status: Any = bot.get_command("status").callback  # type: ignore[union-attr]

            alive = _Ctx()
            await status(alive)
            assert "Worker" in alive.sent[0]
            assert "parado" in alive.sent[0]  # nenhum on_ready ainda: honesto

            bot.loop = asyncio.get_running_loop()
            await bot.__getattribute__("on_ready")()
            running = _Ctx()
            await status(running)
            assert "rodando" in running.sent[0]

    asyncio.run(scenario())
