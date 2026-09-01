"""A/B/C/CL.5 - durabilidade e observabilidade do CAMINHO INTERATIVO.

Segundo smoke real: a analise correu pelo caminho interativo (coorte quente),
o Discord recusou o anexo com 403/50013 e o relatorio se perdeu — enquanto o
fallback afirmava ao usuario que o resultado fora preservado. O primeiro RC
tinha coberto apenas o caminho da fila.

CL.5: um TERCEIRO incidente de soak (o Discord fazendo preview do `.html`
anexado como texto puro) eliminou o attachment por completo — a entrega
agora é resumo compacto (report/discord_summary.py) + capability URL
(bot/report_links.py), servida por bot/report_server.py. Sem fallback
textual: uma falha de entrega (D) usa a semântica de retry já existente,
nunca uma segunda mensagem inventada aqui.

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
from botgitgud.analysis.pipeline import AnalysisResult
from botgitgud.bot.delivery import (
    DeliveryContext,
    ReportDeliveryConfig,
    context_from_discord,
    deliver_report_link,
)
from botgitgud.bot.report_links import ReportLinkStore
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
_BASE_URL = "https://botgitgud.duckdns.org"


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
        top_actions=(),
    )


def _deps(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        store=Store(tmp_path),
        client=SimpleNamespace(),
        settings=SimpleNamespace(
            data_dir=tmp_path,
            bot_stop_poll_interval_s=1.0,
            # CL.5: build_bot() agora exige isto — validado uma vez no boot.
            report_public_base_url=_BASE_URL,
            report_server_host="127.0.0.1",
            report_server_port=0,
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
        async def send(self, content: str, **kwargs: Any) -> None:
            seen_on_send["artifact_existed"] = artifact.is_file()
            await super().send(content, **kwargs)

    _run_analisar(deps, _CheckingCtx(), monkeypatch)
    deps.store.close()

    assert seen_on_send["artifact_existed"] is True


def test_interactive_report_persists_even_when_discord_send_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A regressao exata do segundo smoke: o artefato sobrevive a um
    403/50013. CL.5: sem fallback textual — a falha só é registrada
    (política D), nunca mascarada por uma segunda mensagem.
    """
    deps = _deps(tmp_path)
    ctx = _Ctx(fail=_forbidden())

    _run_analisar(deps, ctx, monkeypatch)
    deps.store.close()

    artifact = report_path_for(tmp_path, interactive_artifact_id("gCXVMN86PwYhyaDd", 9, "Zilbag"))
    assert artifact.is_file()
    assert "<html" in artifact.read_text(encoding="utf-8")
    assert ctx.sent == []  # sem fallback: a falha de envio não gera segunda mensagem


def test_successful_interactive_delivery_never_leaks_a_local_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps = _deps(tmp_path)
    ctx = _Ctx()
    _run_analisar(deps, ctx, monkeypatch)
    deps.store.close()
    assert len(ctx.sent) == 1
    content = ctx.sent[0][0]
    assert str(tmp_path) not in content
    assert "reports" not in content
    assert _BASE_URL in content  # a URL pública, nunca um caminho local


# -- A.5: falha de persistência nunca afirma preservação ---------------------------


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
    message, kwargs = ctx.sent[0]
    assert "file" not in kwargs and "files" not in kwargs
    assert "não consegui guardar" in message


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


# -- A.6: redelivery sem reanalise (link, não attachment) ---------------------------


def test_interactive_artifact_can_be_redelivered(tmp_path: Path) -> None:
    artifact_id = interactive_artifact_id("R", 1, "P")
    persist_report(tmp_path, artifact_id, "<html>x</html>")
    config = ReportDeliveryConfig(
        link_store=ReportLinkStore(Store(tmp_path)), data_dir=tmp_path, public_base_url=_BASE_URL
    )
    ctx = _Ctx()
    outcome = asyncio.run(
        deliver_report_link(
            ctx,
            artifact_id=artifact_id,
            config=config,
            context=DeliveryContext(channel_id=str(CHANNEL_ID)),
        )
    )
    assert outcome.delivered
    assert _BASE_URL in ctx.sent[0][0]
    assert "file" not in ctx.sent[0][1]


def test_interactive_redelivery_makes_zero_wcl_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    def explode(*_a: object, **_k: object) -> None:
        pytest.fail("redelivery must never touch the network")

    monkeypatch.setattr(httpx.Client, "send", explode)
    monkeypatch.setattr(httpx.Client, "request", explode)
    artifact_id = interactive_artifact_id("R", 1, "P")
    persist_report(tmp_path, artifact_id, "<html/>")
    config = ReportDeliveryConfig(
        link_store=ReportLinkStore(Store(tmp_path)), data_dir=tmp_path, public_base_url=_BASE_URL
    )
    outcome = asyncio.run(
        deliver_report_link(
            _Ctx(), artifact_id=artifact_id, config=config, context=DeliveryContext()
        )
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
