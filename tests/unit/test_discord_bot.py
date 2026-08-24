from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import botgitgud.bot.discord_bot as discord_module
from botgitgud.bot.discord_bot import _enqueue_message, _notify_outcome, parse_report_input
from botgitgud.bot.job_models import EnqueueResult, Job, now_utc_naive
from botgitgud.bot.ops_snapshot import read_snapshot
from botgitgud.bot.worker import JobOutcome
from botgitgud.errors import (
    ApiError,
    CohortNotReady,
    FightNotFound,
    InsufficientCohort,
    PlayerNotFound,
    ScopeRejected,
)
from botgitgud.ingest.store import Store
from botgitgud.report.text import ReportHeader


def _job(job_type: str = "analyze", status: str = "running") -> Job:
    return Job(
        job_id="job-1",
        job_type=job_type,  # type: ignore[arg-type]
        dedup_key="ABCDEFGHIJKLMNOP:1:Zarad",
        discord_user_id="123",
        discord_channel_id="456",
        status=status,  # type: ignore[arg-type]
        created_at=now_utc_naive(),
        started_at=now_utc_naive() if status == "running" else None,
        finished_at=None,
        error=None,
        report_path=None,
    )


class _Channel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Any | None]] = []

    async def send(self, message: str, *, file: Any | None = None) -> None:
        self.sent.append((message, file))


class _Bot:
    def __init__(self, channel: _Channel | None = None) -> None:
        self.channel = channel

    def get_channel(self, channel_id: int) -> _Channel | None:
        assert channel_id == 456
        return self.channel


class _Context(_Channel):
    def __init__(self) -> None:
        super().__init__()
        self.author = SimpleNamespace(id=123)
        self.channel = SimpleNamespace(id=456)


class _ImmediateLoop:
    async def run_in_executor(self, _executor: object, call: Any, *args: object) -> Any:
        return call(*args)


def _deps(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        store=Store(tmp_path),
        client=SimpleNamespace(),
        # D-34: o worker loop e o on_ready publicam o ops-snapshot em data_dir.
        settings=SimpleNamespace(data_dir=tmp_path),
    )


def test_parse_report_input_url_bare_and_invalid() -> None:
    assert parse_report_input("https://www.warcraftlogs.com/reports/ABCDEFGHIJKLMNOP?fight=7") == (
        "ABCDEFGHIJKLMNOP",
        7,
    )
    assert parse_report_input("ABCDEFGHIJKLMNOP") == ("ABCDEFGHIJKLMNOP", None)
    assert parse_report_input("curto") == ("curto", None)


def test_enqueue_message_new_deduped_and_rejected() -> None:
    job = _job()
    assert "posição 2" in _enqueue_message(EnqueueResult(job, False, queue_position=2))
    assert "já está na fila" in _enqueue_message(EnqueueResult(job, True))
    assert _enqueue_message(EnqueueResult(None, False, rejected_reason="limite")) == "❌ limite"


def test_notify_analyze_success_sends_summary_and_html_once() -> None:
    channel = _Channel()
    outcome = JobOutcome(_job(), True, "resumo curto", html_report="<html></html>")
    asyncio.run(_notify_outcome(_Bot(channel), outcome))  # type: ignore[arg-type]
    assert len(channel.sent) == 1
    message, attachment = channel.sent[0]
    assert "resumo curto" in message
    assert attachment is not None
    assert attachment.filename == "relatorio.html"


def test_notify_build_failure_and_requeued_contracts() -> None:
    channel = _Channel()
    asyncio.run(_notify_outcome(_Bot(channel), JobOutcome(_job("build_cohort"), True, "feito")))  # type: ignore[arg-type]
    asyncio.run(_notify_outcome(_Bot(channel), JobOutcome(_job(), False, "falhou")))  # type: ignore[arg-type]
    asyncio.run(
        _notify_outcome(_Bot(channel), JobOutcome(_job(), False, "aguarde", requeued=True))  # type: ignore[arg-type]
    )
    assert [message for message, _ in channel.sent] == ["<@123> ✅ feito", "<@123> ❌ falhou"]


def test_worker_loop_does_not_check_budget_when_queue_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def stop(_seconds: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(discord_module.asyncio, "sleep", stop)
    monkeypatch.setattr(
        discord_module,
        "_current_budget",
        lambda _deps: pytest.fail("budget must not be checked for an empty queue"),
    )
    queue = SimpleNamespace(list_active=lambda: [])
    deps = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    worker_loop: Any = discord_module._worker_loop
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_loop(SimpleNamespace(), deps, queue))


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (PlayerNotFound("x"), "não foi encontrado"),
        (FightNotFound("x"), "não foi encontrado"),
        (ScopeRejected("spec fora do escopo"), "spec fora do escopo"),
        (
            InsufficientCohort("x", n_members=2, minimum_required=8),
            "2 logs, mínimo 8",
        ),
        (ApiError("x"), "Erro ao consultar a API"),
    ],
)
def test_analisar_translates_expected_errors_without_propagating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, expected: str
) -> None:
    deps = _deps(tmp_path)
    bot = discord_module.build_bot(deps)  # type: ignore[arg-type]
    command = bot.get_command("analisar")
    assert command is not None
    callback: Any = command.callback
    monkeypatch.setattr(
        discord_module, "run_analysis", lambda *_a, **_k: (_ for _ in ()).throw(error)
    )

    async def invoke() -> None:
        monkeypatch.setattr(discord_module.asyncio, "get_running_loop", lambda: _ImmediateLoop())
        ctx = _Context()
        await callback(ctx, "Zarad", "ABCDEFGHIJKLMNOP?fight=1")
        assert expected in ctx.sent[0][0]

    asyncio.run(invoke())
    deps.store.close()


def test_analisar_cohort_miss_enqueues_and_status_reports_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps = _deps(tmp_path)
    bot = discord_module.build_bot(deps)  # type: ignore[arg-type]
    analyze = bot.get_command("analisar")
    status = bot.get_command("status")
    assert analyze is not None and status is not None
    analyze_callback: Any = analyze.callback
    status_callback: Any = status.callback
    monkeypatch.setattr(
        discord_module,
        "run_analysis",
        lambda *_a, **_k: (_ for _ in ()).throw(CohortNotReady("cold")),
    )

    async def invoke() -> None:
        monkeypatch.setattr(discord_module.asyncio, "get_running_loop", lambda: _ImmediateLoop())
        analyze_ctx = _Context()
        await analyze_callback(analyze_ctx, "Zarad", "ABCDEFGHIJKLMNOP?fight=1")
        assert "job enfileirado" in analyze_ctx.sent[0][0]
        status_ctx = _Context()
        await status_callback(status_ctx)
        assert "queued" in status_ctx.sent[0][0]

    asyncio.run(invoke())
    deps.store.close()


def test_status_empty_queue(tmp_path: Path) -> None:
    deps = _deps(tmp_path)
    bot = discord_module.build_bot(deps)  # type: ignore[arg-type]
    status = bot.get_command("status")
    assert status is not None
    callback: Any = status.callback

    async def invoke() -> None:
        ctx = _Context()
        await callback(ctx)
        assert ctx.sent == [("📋 Fila vazia — nenhum job ativo.", None)]

    asyncio.run(invoke())
    deps.store.close()


def test_analisar_happy_path_sends_summary_and_attachment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps = _deps(tmp_path)
    bot = discord_module.build_bot(deps)  # type: ignore[arg-type]
    command = bot.get_command("analisar")
    assert command is not None
    callback: Any = command.callback
    result = SimpleNamespace(
        header=ReportHeader("Zarad", "Boss", "Warlock", "Demonology", 20, 300.0, 360.0),
        comparisons=(),
        manifest=None,
        build_divergence=None,
        performance=None,
        dps_gap=None,
        top_actions=(),
    )
    monkeypatch.setattr(discord_module, "run_analysis", lambda *_a, **_k: result)

    async def invoke() -> None:
        monkeypatch.setattr(discord_module.asyncio, "get_running_loop", lambda: _ImmediateLoop())
        ctx = _Context()
        await callback(ctx, "Zarad", "ABCDEFGHIJKLMNOP?fight=1")
        message, attachment = ctx.sent[0]
        assert "TOP 3 AÇÕES COM GANHO" in message
        assert attachment is not None
        assert attachment.filename == "relatorio.html"

    asyncio.run(invoke())
    deps.store.close()


def test_worker_loop_survives_budget_api_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def one_iteration(_seconds: float) -> None:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(discord_module.asyncio, "sleep", one_iteration)
    monkeypatch.setattr(
        discord_module, "_current_budget", lambda _deps: (_ for _ in ()).throw(ApiError("down"))
    )
    queue = SimpleNamespace(list_active=lambda: [_job(status="queued")])
    deps = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    worker_loop: Any = discord_module._worker_loop
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_loop(SimpleNamespace(), deps, queue))
    assert calls == 2


def test_worker_loop_publishes_the_ops_snapshot_each_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-34: é este publish que faz `ops-status` responder com o bot no ar."""
    calls = 0

    async def one_iteration(_seconds: float) -> None:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(discord_module.asyncio, "sleep", one_iteration)
    queue = SimpleNamespace(list_active=lambda: [_job(status="running")])
    deps = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    worker_loop: Any = discord_module._worker_loop
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_loop(SimpleNamespace(), deps, queue))

    snapshot = read_snapshot(tmp_path)
    assert snapshot is not None
    assert (snapshot.queued, snapshot.running) == (0, 1)
    assert snapshot.points_remaining is None  # fila sem queued: nunca consultou orçamento


def test_snapshot_write_failure_never_kills_the_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def one_iteration(_seconds: float) -> None:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(discord_module.asyncio, "sleep", one_iteration)
    monkeypatch.setattr(
        discord_module,
        "write_snapshot",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("disco cheio")),
    )
    queue = SimpleNamespace(list_active=lambda: [])
    deps = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    worker_loop: Any = discord_module._worker_loop
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_loop(SimpleNamespace(), deps, queue))
    assert calls == 2


def test_on_ready_starts_worker_only_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    deps = _deps(tmp_path)
    starts = 0

    async def worker_once(*_args: object) -> None:
        nonlocal starts
        starts += 1

    monkeypatch.setattr(discord_module, "_worker_loop", worker_once)
    bot = discord_module.build_bot(deps)  # type: ignore[arg-type]
    on_ready: Any = bot.__getattribute__("on_ready")

    async def reconnect_twice() -> None:
        bot.loop = asyncio.get_running_loop()
        await on_ready()
        await asyncio.sleep(0)
        await on_ready()
        await asyncio.sleep(0)

    asyncio.run(reconnect_twice())
    assert starts == 1
    deps.store.close()
