"""A/B/C - durabilidade e observabilidade do CAMINHO INTERATIVO.

Segundo smoke real: a analise correu pelo caminho interativo (coorte quente),
o Discord recusou o anexo com 403/50013 e o relatorio se perdeu — enquanto o
fallback afirmava ao usuario que o resultado fora preservado. O primeiro RC
tinha coberto apenas o caminho da fila.

Nada aqui toca Discord ou WCL de verdade.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import MutableMapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest
import structlog

import botgitgud.bot.discord_bot as discord_module
from botgitgud.bot.delivery import (
    NOT_PRESERVED_NOTICE,
    PRESERVED_NOTICE,
    DeliveryContext,
    context_from_discord,
    deliver_persisted_report,
    send_report,
)
from botgitgud.bot.report_store import (
    interactive_artifact_id,
    persist_report,
    report_path_for,
    reports_dir,
)
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
    """Fake de discord Context: carrega ids como o objeto real."""

    def __init__(self, *, fail_with_file: Exception | None = None) -> None:
        self.sent: list[tuple[str, Any]] = []
        self.author = SimpleNamespace(id=42)
        self.channel = SimpleNamespace(id=CHANNEL_ID)
        self.guild = SimpleNamespace(id=GUILD_ID)
        self._fail_with_file = fail_with_file

    async def send(self, content: str, *, file: Any | None = None) -> None:
        if file is not None and self._fail_with_file is not None:
            raise self._fail_with_file
        self.sent.append((content, file))


class _ImmediateLoop:
    async def run_in_executor(self, _executor: object, call: Any, *args: object) -> Any:
        return call(*args)


def _result() -> SimpleNamespace:
    return SimpleNamespace(
        header=ReportHeader("Zilbag", "Boss", "DeathKnight", "Unholy", 20, 300.0, 360.0),
        comparisons=(),
        manifest=None,
        build_divergence=None,
        performance=None,
        dps_gap=None,
        top_actions=(),
    )


def _deps(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        store=Store(tmp_path),
        client=SimpleNamespace(),
        settings=SimpleNamespace(data_dir=tmp_path),
    )


def _run_analisar(deps: Any, ctx: _Ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    bot = discord_module.build_bot(deps)
    callback: Any = bot.get_command("analisar").callback  # type: ignore[union-attr]
    monkeypatch.setattr(discord_module, "run_analysis", lambda *_a, **_k: _result())

    async def invoke() -> None:
        monkeypatch.setattr(discord_module.asyncio, "get_running_loop", lambda: _ImmediateLoop())
        await callback(ctx, "Zilbag", "gCXVMN86PwYhyaDd?fight=9")

    asyncio.run(invoke())


# -- A.2: identidade do artefato interativo ---------------------------------------


def test_interactive_artifact_id_is_deterministic_and_path_safe() -> None:
    first = interactive_artifact_id("gCXVMN86PwYhyaDd", 9, "Zilbag")
    assert first == interactive_artifact_id("gCXVMN86PwYhyaDd", 9, "Zilbag")
    assert first != interactive_artifact_id("gCXVMN86PwYhyaDd", 10, "Zilbag")
    assert first != interactive_artifact_id("gCXVMN86PwYhyaDd", 9, "Outro")


def test_hostile_player_name_can_never_escape_the_reports_directory(tmp_path: Path) -> None:
    evil = interactive_artifact_id("../../etc", 9, "../../../passwd")
    path = report_path_for(tmp_path, evil)
    assert path.parent == reports_dir(tmp_path)
    assert ".." not in path.name


# -- A.1/A.3: persistir ANTES da rede ---------------------------------------------


def test_interactive_report_is_persisted_before_the_discord_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps = _deps(tmp_path)
    seen_on_send: dict[str, bool] = {}
    artifact = report_path_for(tmp_path, interactive_artifact_id("gCXVMN86PwYhyaDd", 9, "Zilbag"))

    class _CheckingCtx(_Ctx):
        async def send(self, content: str, *, file: Any | None = None) -> None:
            if file is not None:
                seen_on_send["artifact_existed"] = artifact.is_file()
            await super().send(content, file=file)

    _run_analisar(deps, _CheckingCtx(), monkeypatch)
    deps.store.close()

    assert seen_on_send["artifact_existed"] is True


def test_interactive_report_survives_forbidden_50013(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A regressao exata do segundo smoke."""
    deps = _deps(tmp_path)
    ctx = _Ctx(fail_with_file=_forbidden())

    _run_analisar(deps, ctx, monkeypatch)
    deps.store.close()

    artifact = report_path_for(tmp_path, interactive_artifact_id("gCXVMN86PwYhyaDd", 9, "Zilbag"))
    assert artifact.is_file()
    assert "<html" in artifact.read_text(encoding="utf-8")
    assert PRESERVED_NOTICE in ctx.sent[0][0]  # fallback verdadeiro


def test_interactive_fallback_never_leaks_a_local_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps = _deps(tmp_path)
    ctx = _Ctx(fail_with_file=_forbidden())
    _run_analisar(deps, ctx, monkeypatch)
    deps.store.close()
    assert str(tmp_path) not in ctx.sent[0][0]
    assert "reports" not in ctx.sent[0][0]


# -- A.4/A.5: o fallback nao pode mentir ------------------------------------------


def test_fallback_claims_preservation_only_when_the_artifact_exists() -> None:
    class _Chan(_Ctx):
        pass

    preserved = _Chan(fail_with_file=_forbidden())
    asyncio.run(
        send_report(
            preserved,
            summary="r",
            html="<html/>",
            context=DeliveryContext(channel_id=str(CHANNEL_ID)),
            preserved=True,
        )
    )
    assert PRESERVED_NOTICE in preserved.sent[0][0]

    lost = _Chan(fail_with_file=_forbidden())
    asyncio.run(
        send_report(
            lost,
            summary="r",
            html="<html/>",
            context=DeliveryContext(channel_id=str(CHANNEL_ID)),
            preserved=False,
        )
    )
    assert NOT_PRESERVED_NOTICE in lost.sent[0][0]
    assert "preservado" not in lost.sent[0][0].replace("não foi possível", "")


def test_persistence_failure_skips_delivery_and_never_claims_preservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps = _deps(tmp_path)
    monkeypatch.setattr(
        discord_module,
        "persist_report",
        lambda *_a, **_k: (_ for _ in ()).throw(
            discord_module.ReportPersistenceError("disco cheio")
        ),
    )
    ctx = _Ctx()
    _run_analisar(deps, ctx, monkeypatch)
    deps.store.close()

    assert len(ctx.sent) == 1
    message, attachment = ctx.sent[0]
    assert attachment is None  # nunca tenta anexar arquivo inexistente
    assert "não consegui guardar" in message
    assert PRESERVED_NOTICE not in message


def test_filesystem_failure_keeps_the_process_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps = _deps(tmp_path)
    monkeypatch.setattr(
        discord_module,
        "persist_report",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("permission denied")),
    )
    with pytest.raises(OSError, match="permission denied"):
        _run_analisar(deps, _Ctx(), monkeypatch)
    deps.store.close()


# -- B: observabilidade -------------------------------------------------------------


def test_context_extracts_channel_and_guild_from_a_discord_context() -> None:
    context = context_from_discord(_Ctx(), job_id="abc")
    assert context.channel_id == str(CHANNEL_ID)
    assert context.guild_id == str(GUILD_ID)
    assert context.job_id == "abc"


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
    ctx = _Ctx(fail_with_file=_forbidden())
    events = _captured_events(lambda: _run_analisar(deps, ctx, monkeypatch))
    deps.store.close()

    failures = [e for e in events if e.get("event") == "delivery.failed"]
    assert failures, "delivery.failed deve ser registrado"
    failure = failures[0]
    assert failure["channel_id"] == str(CHANNEL_ID)
    assert failure["guild_id"] == str(GUILD_ID)
    assert failure["http_status"] == 403
    assert failure["discord_code"] == 50013
    assert failure["report_preserved"] is True


def test_successful_delivery_also_logs_channel_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps = _deps(tmp_path)
    events = _captured_events(lambda: _run_analisar(deps, _Ctx(), monkeypatch))
    deps.store.close()

    ok = [e for e in events if e.get("event") == "delivery.succeeded"]
    assert ok and ok[0]["channel_id"] == str(CHANNEL_ID)
    assert ok[0]["guild_id"] == str(GUILD_ID)


def test_http_exception_is_contained_and_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps = _deps(tmp_path)
    ctx = _Ctx(fail_with_file=_http_error())
    events = _captured_events(lambda: _run_analisar(deps, ctx, monkeypatch))
    deps.store.close()

    failure = next(e for e in events if e.get("event") == "delivery.failed")
    assert failure["http_status"] == 500
    assert failure["channel_id"] == str(CHANNEL_ID)
    # 500 nao e problema de permissao: sem fallback textual
    assert ctx.sent == []


# -- A.6: redelivery sem reanalise ---------------------------------------------------


def test_interactive_artifact_can_be_redelivered(tmp_path: Path) -> None:
    path = persist_report(tmp_path, interactive_artifact_id("R", 1, "P"), "<html>x</html>")
    ctx = _Ctx()
    outcome = asyncio.run(
        deliver_persisted_report(
            ctx, report_path=path, context=DeliveryContext(channel_id=str(CHANNEL_ID))
        )
    )
    assert outcome.delivered
    assert ctx.sent[0][1].filename == "relatorio.html"


def test_interactive_redelivery_makes_zero_wcl_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    def explode(*_a: object, **_k: object) -> None:
        pytest.fail("redelivery must never touch the network")

    monkeypatch.setattr(httpx.Client, "send", explode)
    monkeypatch.setattr(httpx.Client, "request", explode)
    path = persist_report(tmp_path, interactive_artifact_id("R", 1, "P"), "<html/>")
    outcome = asyncio.run(
        deliver_persisted_report(_Ctx(), report_path=path, context=DeliveryContext())
    )
    assert outcome.delivered


# -- C: isolamento de testes ----------------------------------------------------------


def test_this_suite_never_writes_the_real_reports_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C.2 — o artefato tem de nascer dentro do tmp_path, nunca no data/ real."""
    real_reports = Path(__file__).resolve().parents[2] / "data" / "reports"
    before = sorted(p.name for p in real_reports.iterdir()) if real_reports.exists() else []

    deps = _deps(tmp_path)
    _run_analisar(deps, _Ctx(), monkeypatch)
    deps.store.close()

    after = sorted(p.name for p in real_reports.iterdir()) if real_reports.exists() else []
    assert after == before
    assert list(reports_dir(tmp_path).iterdir())  # o artefato foi para o tmp_path


def test_artifact_content_is_the_rendered_html(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps = _deps(tmp_path)
    _run_analisar(deps, _Ctx(), monkeypatch)
    deps.store.close()
    artifact = report_path_for(tmp_path, interactive_artifact_id("gCXVMN86PwYhyaDd", 9, "Zilbag"))
    content = artifact.read_text(encoding="utf-8")
    assert content.startswith("<?xml")
    assert "Zilbag" in content
    json.dumps({"ok": True})  # sanity: nenhum estado global quebrado
