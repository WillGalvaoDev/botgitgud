from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import botgitgud.bot.discord_bot as discord_module
from botgitgud.bot.discord_bot import _enqueue_message, _notify_outcome, parse_report_input
from botgitgud.bot.job_models import BudgetStatus, EnqueueResult, Job, now_utc_naive
from botgitgud.bot.jobs import JobQueue
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


def _job(
    job_type: str = "analyze",
    status: str = "running",
    *,
    deferred_until: Any = None,
    defer_count: int = 0,
) -> Job:
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
        deferred_until=deferred_until,
        defer_count=defer_count,
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
        # bot_stop_poll_interval_s: B6, o on_ready arma o watcher de stop.request.
        settings=SimpleNamespace(data_dir=tmp_path, bot_stop_poll_interval_s=1.0),
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


def test_notify_analyze_success_sends_summary_and_html_once(tmp_path: Path) -> None:
    channel = _Channel()
    outcome = JobOutcome(_job(), True, "resumo curto", html_report="<html></html>")
    with Store(tmp_path) as store:
        asyncio.run(_notify_outcome(_Bot(channel), outcome, JobQueue(store)))
    assert len(channel.sent) == 1
    message, attachment = channel.sent[0]
    assert "resumo curto" in message
    assert attachment is not None
    assert attachment.filename == "relatorio.html"


def test_notify_build_failure_and_requeued_contracts(tmp_path: Path) -> None:
    channel = _Channel()
    with Store(tmp_path) as store:
        queue = JobQueue(store)
        asyncio.run(
            _notify_outcome(_Bot(channel), JobOutcome(_job("build_cohort"), True, "feito"), queue)
        )
        asyncio.run(_notify_outcome(_Bot(channel), JobOutcome(_job(), False, "falhou"), queue))
        asyncio.run(
            _notify_outcome(
                _Bot(channel), JobOutcome(_job(), False, "aguarde", requeued=True), queue
            )
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
        # B2: o usuario precisa saber que a analise continua sozinha e que
        # repetir o comando nao adianta nem acelera nada.
        enqueue_message = analyze_ctx.sent[0][0]
        assert "colocada na fila" in enqueue_message
        assert "repetir o comando" in enqueue_message
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
        message, attachment = ctx.sent[0]
        assert "Fila vazia" in message
        assert attachment is None
        # RC.14: sem on_ready neste teste, o worker realmente nao esta rodando
        assert "Worker" in message

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


# -- B3-fix: regressao real do soak de 2026-08-27 — guard precisa considerar ---------
# deferred_budget vencido, nao so "queued". Job real: 2b3a1091d00c4bc49094ff3231bd5332
# (99/100 refs, deferred_until 2026-08-27T05:34:09Z, nunca retomado sozinho).


def _budget_ok(_deps: object) -> BudgetStatus:
    return BudgetStatus(points_remaining=3600.0, limit_per_hour=3600.0)


def test_worker_loop_resumes_an_expired_deferred_job_without_a_new_queued_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A regressão real: um `deferred_budget` cujo `deferred_until` já passou
    precisa abrir o guard SOZINHO — sem nenhum job `queued` coexistindo.
    Antes da correção, `claim_next()` nunca era sequer chamado neste cenário.
    """
    from datetime import timedelta

    past = now_utc_naive() - timedelta(seconds=1)
    deferred_job = _job(status="deferred_budget", deferred_until=past, defer_count=1)

    calls = 0

    async def one_iteration(_seconds: float) -> None:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise asyncio.CancelledError

    claim_calls: list[object] = []

    def spy_claim_next(budget: object) -> None:
        claim_calls.append(budget)
        return None  # a execução real do job resumido é coberta pelo teste
        # sintético end-to-end em test_worker.py; aqui a prova é que o
        # guard deixou passar sem nenhum job `queued`.

    monkeypatch.setattr(discord_module.asyncio, "sleep", one_iteration)
    monkeypatch.setattr(discord_module, "_current_budget", _budget_ok)
    queue = SimpleNamespace(list_active=lambda: [deferred_job], claim_next=spy_claim_next)
    deps = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    worker_loop: Any = discord_module._worker_loop
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_loop(SimpleNamespace(), deps, queue))

    assert len(claim_calls) == 1


def test_worker_loop_does_not_check_budget_for_a_deferred_job_still_in_the_future(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preserva a otimização original: um `deferred_budget` no futuro não
    deve gastar um budget check a cada tick — só quando `deferred_until`
    realmente vencer.
    """
    from datetime import timedelta

    future = now_utc_naive() + timedelta(hours=1)
    deferred_job = _job(status="deferred_budget", deferred_until=future)

    async def stop(_seconds: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(discord_module.asyncio, "sleep", stop)
    monkeypatch.setattr(
        discord_module,
        "_current_budget",
        lambda _deps: pytest.fail("budget não deve ser checado: deferral ainda no futuro"),
    )
    queue = SimpleNamespace(list_active=lambda: [deferred_job])
    deps = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    worker_loop: Any = discord_module._worker_loop
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_loop(SimpleNamespace(), deps, queue))


def test_worker_loop_ignores_a_future_deferred_job_even_with_a_queued_job_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dois jobs: um `deferred_budget` futuro e um `queued`. O guard abre
    (por causa do `queued`) e `claim_next()` decide sozinho qual roda —
    política inalterada, não retestada aqui.
    """
    from datetime import timedelta

    future = now_utc_naive() + timedelta(hours=1)
    future_job = _job(status="deferred_budget", deferred_until=future)
    queued_job = _job(status="queued")

    calls = 0

    async def one_iteration(_seconds: float) -> None:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise asyncio.CancelledError

    claim_calls: list[object] = []

    def spy_claim_next(budget: object) -> None:
        claim_calls.append(budget)
        return None

    monkeypatch.setattr(discord_module.asyncio, "sleep", one_iteration)
    monkeypatch.setattr(discord_module, "_current_budget", _budget_ok)
    queue = SimpleNamespace(list_active=lambda: [future_job, queued_job], claim_next=spy_claim_next)
    deps = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    worker_loop: Any = discord_module._worker_loop
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_loop(SimpleNamespace(), deps, queue))

    assert len(claim_calls) == 1  # o queued sozinho já abre o guard


def test_worker_loop_opens_the_guard_when_an_expired_deferral_coexists_with_a_queued_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dois jobs elegíveis ao mesmo tempo: um `deferred_budget` vencido e um
    `queued` novo. O guard abre; a ordem entre os dois é política já coberta
    por `claim_next()` em test_jobs.py, não reexaminada aqui.
    """
    from datetime import timedelta

    past = now_utc_naive() - timedelta(seconds=1)
    expired_job = _job(status="deferred_budget", deferred_until=past, defer_count=2)
    queued_job = _job(status="queued")

    calls = 0

    async def one_iteration(_seconds: float) -> None:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise asyncio.CancelledError

    claim_calls: list[object] = []

    def spy_claim_next(budget: object) -> None:
        claim_calls.append(budget)
        return None

    monkeypatch.setattr(discord_module.asyncio, "sleep", one_iteration)
    monkeypatch.setattr(discord_module, "_current_budget", _budget_ok)
    queue = SimpleNamespace(
        list_active=lambda: [expired_job, queued_job], claim_next=spy_claim_next
    )
    deps = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    worker_loop: Any = discord_module._worker_loop
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker_loop(SimpleNamespace(), deps, queue))

    assert len(claim_calls) == 1


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


def test_on_ready_does_not_duplicate_a_live_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RC.7: reconexao do Discord com worker vivo nunca cria um segundo."""
    deps = _deps(tmp_path)
    starts = 0

    async def long_lived_worker(*_args: object) -> None:
        nonlocal starts
        starts += 1
        await asyncio.sleep(3600)

    monkeypatch.setattr(discord_module, "_worker_loop", long_lived_worker)
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


def test_on_ready_revives_a_dead_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RC.6: o incidente real deixou a task morta e o guard booleano da T1.8
    impedia qualquer recuperacao — o sistema ficava sem consumidor ate
    reiniciar o processo. Agora um on_ready posterior recria o worker.
    """
    deps = _deps(tmp_path)
    starts = 0

    async def worker_that_exits(*_args: object) -> None:
        nonlocal starts
        starts += 1

    monkeypatch.setattr(discord_module, "_worker_loop", worker_that_exits)
    bot = discord_module.build_bot(deps)  # type: ignore[arg-type]
    on_ready: Any = bot.__getattribute__("on_ready")

    async def reconnect_after_death() -> None:
        bot.loop = asyncio.get_running_loop()
        await on_ready()
        await asyncio.sleep(0)
        await asyncio.sleep(0)  # worker termina aqui
        await on_ready()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(reconnect_after_death())
    assert starts == 2
    deps.store.close()


# -- B6: watcher de stop-request -----------------------------------------------------


class _FakeCloseBot:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def test_stop_request_watcher_calls_bot_close_once_the_marker_appears(
    tmp_path: Path,
) -> None:
    """O canal de controle e so um arquivo (ops/control.py): a presenca dele
    e o unico gatilho, sem nenhum sinal de SO envolvido.
    """
    from botgitgud.ops.control import request_stop

    bot = _FakeCloseBot()

    async def scenario() -> None:
        watcher = asyncio.ensure_future(
            discord_module._stop_request_watcher(bot, tmp_path, poll_interval_s=0.01)  # type: ignore[arg-type]
        )
        await asyncio.sleep(0.03)
        assert bot.closed is False  # nao fecha sozinho sem o pedido
        request_stop(tmp_path)
        await asyncio.wait_for(watcher, timeout=2.0)

    asyncio.run(scenario())
    assert bot.closed is True


def test_stop_request_watcher_never_deletes_the_marker_itself(tmp_path: Path) -> None:
    """Contrato de ops/control.py: so start-bot-service.ps1 limpa o arquivo —
    nunca quem o le, senao uma corrida entre o watcher e o supervisor poderia
    fazer o segundo achar que ninguem pediu parada.
    """
    from botgitgud.ops.control import request_stop, stop_requested

    bot = _FakeCloseBot()
    request_stop(tmp_path)

    async def scenario() -> None:
        await discord_module._stop_request_watcher(bot, tmp_path, poll_interval_s=0.01)  # type: ignore[arg-type]

    asyncio.run(scenario())
    assert bot.closed is True
    assert stop_requested(tmp_path) is True  # continua la
