from __future__ import annotations

import ast
import asyncio
import inspect
from collections.abc import MutableMapping
from typing import Any

import discord
import pytest
import structlog

import botgitgud.bot.delivery as delivery_module
from botgitgud.analysis.findings import TopPriorities
from botgitgud.bot.delivery import DeliveryContext, deliver_completed_report, send_text
from botgitgud.report.coaching_answer import MAX_DISCORD_COACHING_ANSWER
from botgitgud.report.contract import ConfidenceSummary, ExecutionSection, ReportContract
from botgitgud.report.text import ReportHeader


def _contract() -> ReportContract:
    return ReportContract(
        resultado=ReportHeader(
            char_name="Zarad",
            boss_name="Fallen-King Salhadaar",
            class_name="Warlock",
            spec="Demonology",
            reference_n=20,
            duration_min_s=300.0,
            duration_max_s=360.0,
            player_dps=108297.0,
            player_percentile=57.0,
        ),
        setup=None,
        execucao=ExecutionSection(comparisons=(), performance=None, dps_gap=None),
        top_actions=TopPriorities(),
        confianca=ConfidenceSummary(
            reference_pool_members=40,
            matched_cohort_members=20,
            cohort_warnings=(),
            matched_covariates=(),
            relaxed_covariates=(),
        ),
        manifest=None,
    )


def _context() -> DeliveryContext:
    return DeliveryContext(channel_id="456", guild_id="789", job_id="job-1")


def _http_error(status: int, code: int = 0) -> discord.HTTPException:
    response: Any = type("Response", (), {"status": status, "reason": "error"})()
    return discord.HTTPException(response, {"code": code, "message": "boom"})


class _Channel:
    def __init__(self, failure: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.failure = failure

    async def send(self, content: str, **kwargs: Any) -> None:
        if self.failure is not None:
            raise self.failure
        self.calls.append((content, kwargs))


def _deliver(channel: _Channel) -> Any:
    return asyncio.run(
        deliver_completed_report(
            channel,
            contract=_contract(),
            artifact_id="job-1",
            context=_context(),
        )
    )


def test_completed_delivery_is_one_message_without_attachment_or_url() -> None:
    channel = _Channel()
    outcome = _deliver(channel)
    assert outcome.delivered
    assert len(channel.calls) == 1
    content, kwargs = channel.calls[0]
    assert len(content) <= MAX_DISCORD_COACHING_ANSWER
    assert "http://" not in content and "https://" not in content
    assert not {"file", "files", "attachment", "attachments"} & kwargs.keys()
    assert kwargs["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()


def test_delivery_module_has_no_attachment_send_keywords() -> None:
    tree = ast.parse(inspect.getsource(delivery_module))
    forbidden = {"file", "files", "attachment", "attachments"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            assert forbidden.isdisjoint(keyword.arg for keyword in node.keywords)


def test_renderer_failure_is_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(_contract: ReportContract) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(delivery_module, "render_coaching_answer", explode)
    channel = _Channel()
    outcome = _deliver(channel)
    assert outcome.status == "failed"
    assert channel.calls == []


def test_discord_failure_is_contained() -> None:
    channel = _Channel(_http_error(403, 50013))
    outcome = _deliver(channel)
    assert outcome.status == "failed"
    assert outcome.http_status == 403
    assert outcome.discord_code == 50013


def test_send_text_contains_http_errors() -> None:
    outcome = asyncio.run(send_text(_Channel(_http_error(503)), content="oi", context=_context()))
    assert outcome.status == "failed"
    assert outcome.http_status == 503


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


def test_completed_delivery_logs_no_url_or_message_content() -> None:
    """RB-6/higiene de log: a entrega concluída nunca registra o conteúdo
    da mensagem de coaching (nem qualquer fragmento dela) em log
    estruturado — só metadado (`artifact_id`, ids de contexto, status).
    """
    channel = _Channel()
    events = _captured_events(lambda: _deliver(channel))

    assert channel.calls  # a mensagem foi de fato enviada
    sent_content = channel.calls[0][0]
    assert sent_content  # sanity: há de fato conteúdo para checar vazamento
    for event in events:
        blob = repr(event)
        assert sent_content not in blob, f"conteúdo da mensagem vazou em log: {event}"
