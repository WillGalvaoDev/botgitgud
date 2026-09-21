"""B/CL.5 - observabilidade do CAMINHO INTERATIVO (`!analisar`).

M32 aposentou a persistência de HTML, a capability de link e o servidor de
relatórios; o caminho interativo hoje entrega só a resposta de coaching
(`deliver_completed_report`), sem fallback textual e sem anexo. O que
continua vivo em produção — extrair contexto do objeto do Discord
(`context_from_discord`) e conter/logar uma falha de envio — precisa
continuar coberto em runtime, não só por um AST estático de uma chamada.

Nada aqui toca Discord ou WCL de verdade.
"""

from __future__ import annotations

import asyncio
from collections.abc import MutableMapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest
import structlog

import botgitgud.bot.discord_bot as discord_module
from botgitgud.analysis.findings import TopPriorities
from botgitgud.analysis.pipeline import AnalysisResult
from botgitgud.bot.delivery import context_from_discord
from botgitgud.ingest.store import Store
from botgitgud.report.text import ReportHeader

CHANNEL_ID = 123456789
GUILD_ID = 987654321


def _forbidden(code: int = 50013) -> discord.Forbidden:
    response: Any = type("R", (), {"status": 403, "reason": "Forbidden"})()
    return discord.Forbidden(response, {"code": code, "message": "Missing Permissions"})


def _http_error() -> discord.HTTPException:
    response: Any = type("R", (), {"status": 500, "reason": "Server Error"})()
    return discord.HTTPException(response, {"code": 0, "message": "boom"})


class _Ctx:
    """Fake de discord Context: carrega ids como o objeto real. Captura
    TODOS os kwargs de `send` (nunca só `file=`) — CL.5 nunca deveria
    passar `file`/`files`/`attachments`, e um teste que só olhasse `file`
    não pegaria essa regressão.
    """

    def __init__(self, *, fail: Exception | None = None) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.author = SimpleNamespace(id=42)
        self.channel = SimpleNamespace(id=CHANNEL_ID)
        self.guild = SimpleNamespace(id=GUILD_ID)
        self._fail = fail

    async def send(self, content: str, **kwargs: Any) -> None:
        if self._fail is not None:
            raise self._fail
        self.sent.append((content, kwargs))


class _ImmediateLoop:
    async def run_in_executor(self, _executor: object, call: Any, *args: object) -> Any:
        return call(*args)


def _result() -> AnalysisResult:
    # RP.3: o caminho real constrói um `ReportContract` a partir deste
    # objeto, então o duplo precisa ser o tipo de produção, não um
    # SimpleNamespace parcial.
    return AnalysisResult(
        header=ReportHeader("Zilbag", "Boss", "DeathKnight", "Unholy", 20, 300.0, 360.0),
        comparisons=(),
        manifest=None,  # type: ignore[arg-type]
        setup_analysis=None,
        # EB.6: um `AnalysisResult` sem identidade de benchmark é um no-op
        # explícito para o gatilho — este duplo não exercita o benchmark.
        benchmark_target=None,
        benchmark_policy=None,
        performance=None,
        dps_gap=None,
        top_actions=TopPriorities(),
    )


def _deps(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        store=Store(tmp_path),
        client=SimpleNamespace(),
        settings=SimpleNamespace(
            data_dir=tmp_path,
            bot_stop_poll_interval_s=1.0,
        ),
    )


def _run_analisar(deps: Any, ctx: _Ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    bot = discord_module.build_bot(deps)
    callback: Any = bot.get_command("analisar").callback  # type: ignore[union-attr]
    monkeypatch.setattr(discord_module, "run_analysis", lambda *_a, **_k: _result())

    async def invoke() -> None:
        monkeypatch.setattr(discord_module.asyncio, "get_running_loop", lambda: _ImmediateLoop())
        await callback(ctx, "Zilbag", "gCXVMN86PwYhyaDd?fight=9")

    asyncio.run(invoke())


# -- context_from_discord: sem nenhum teste em lugar nenhum -----------------------


def test_context_extracts_channel_and_guild_from_a_discord_context() -> None:
    context = context_from_discord(_Ctx(), job_id="abc")
    assert context.channel_id == str(CHANNEL_ID)
    assert context.guild_id == str(GUILD_ID)
    assert context.job_id == "abc"


# -- observabilidade da entrega -----------------------------------------------------


def _captured_events(fn: Any) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    def sink(_logger: Any, _name: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
        events.append(dict(event))
        return event

    old = structlog.get_config()["processors"]
    structlog.configure(processors=[sink, structlog.processors.JSONRenderer()])
    try:
        fn()
    finally:
        structlog.configure(processors=old)
    return events


def test_forbidden_logs_channel_id_http_status_and_discord_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps = _deps(tmp_path)
    ctx = _Ctx(fail=_forbidden())
    events = _captured_events(lambda: _run_analisar(deps, ctx, monkeypatch))
    deps.store.close()

    failures = [e for e in events if e.get("event") == "report_delivery.send_failed"]
    assert failures, "report_delivery.send_failed deve ser registrado"
    failure = failures[0]
    assert failure["channel_id"] == str(CHANNEL_ID)
    assert failure["guild_id"] == str(GUILD_ID)
    assert failure["http_status"] == 403
    assert failure["discord_code"] == 50013
    assert failure["reason"] == "forbidden"
    assert ctx.sent == []  # sem fallback: a falha de envio não gera segunda mensagem


def test_successful_delivery_also_logs_channel_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps = _deps(tmp_path)
    events = _captured_events(lambda: _run_analisar(deps, _Ctx(), monkeypatch))
    deps.store.close()

    ok = [e for e in events if e.get("event") == "report_delivery.sent"]
    assert ok and ok[0]["channel_id"] == str(CHANNEL_ID)
    assert ok[0]["guild_id"] == str(GUILD_ID)


def test_http_exception_is_contained_and_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps = _deps(tmp_path)
    ctx = _Ctx(fail=_http_error())
    events = _captured_events(lambda: _run_analisar(deps, ctx, monkeypatch))
    deps.store.close()

    failure = next(e for e in events if e.get("event") == "report_delivery.send_failed")
    assert failure["http_status"] == 500
    assert failure["channel_id"] == str(CHANNEL_ID)
    assert ctx.sent == []


# -- guarda arquitetural complementar (AST) -----------------------------------------


def test_interactive_analysis_delivers_once_without_file_keyword() -> None:
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(discord_module.build_bot))
    deliveries = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "deliver_completed_report"
    ]
    assert len(deliveries) == 1
    assert {keyword.arg for keyword in deliveries[0].keywords} == {
        "contract",
        "artifact_id",
        "context",
    }
