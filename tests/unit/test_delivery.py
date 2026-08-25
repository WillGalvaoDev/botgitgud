"""RC.3/RC.8/RC.9/RC.10 — fronteira de entrega.

Regressão do incidente real: análise concluída + `discord.Forbidden` 403/50013
no envio do HTML. Nada aqui toca o Discord de verdade.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import discord
import pytest

from botgitgud.bot.delivery import (
    FALLBACK_NOTICE,
    deliver_existing_report,
    send_report,
    send_text,
)
from botgitgud.bot.job_models import Job, now_utc_naive
from botgitgud.bot.report_store import persist_report


def _job(report_path: str | None = None) -> Job:
    return Job(
        job_id="job-1",
        job_type="analyze",
        dedup_key="ABCDEFGHIJKLMNOP:1:Zilbag",
        discord_user_id="123",
        discord_channel_id="456",
        status="done",
        created_at=now_utc_naive(),
        started_at=now_utc_naive(),
        finished_at=now_utc_naive(),
        error=None,
        report_path=report_path,
    )


def _forbidden(code: int = 50013) -> discord.Forbidden:
    response: Any = type("R", (), {"status": 403, "reason": "Forbidden"})()
    return discord.Forbidden(response, {"code": code, "message": "Missing Permissions"})


def _http_error(status: int = 500, code: int = 0) -> discord.HTTPException:
    response: Any = type("R", (), {"status": status, "reason": "Server Error"})()
    return discord.HTTPException(response, {"code": code, "message": "boom"})


class _Channel:
    """Falha conforme programado; registra o que foi enviado."""

    def __init__(
        self, *, fail_with_file: Exception | None = None, fail_always: Exception | None = None
    ) -> None:
        self.sent: list[tuple[str, Any]] = []
        self._fail_with_file = fail_with_file
        self._fail_always = fail_always

    async def send(self, content: str, *, file: Any | None = None) -> None:
        if self._fail_always is not None:
            raise self._fail_always
        if file is not None and self._fail_with_file is not None:
            raise self._fail_with_file
        self.sent.append((content, file))


def test_normal_delivery_sends_summary_and_attachment() -> None:
    channel = _Channel()
    out = asyncio.run(
        send_report(channel, job=_job(), summary="resumo", html="<html/>", mention="<@123>")
    )
    assert out.delivered
    assert len(channel.sent) == 1
    content, file = channel.sent[0]
    assert "resumo" in content
    assert file.filename == "relatorio.html"


def test_forbidden_50013_is_reported_as_failed_delivery_not_raised() -> None:
    channel = _Channel(fail_with_file=_forbidden())
    out = asyncio.run(
        send_report(channel, job=_job(), summary="r", html="<html/>", mention="<@123>")
    )
    assert out.status == "failed"
    assert out.http_status == 403
    assert out.discord_code == 50013


def test_forbidden_triggers_the_short_text_fallback() -> None:
    channel = _Channel(fail_with_file=_forbidden())
    out = asyncio.run(
        send_report(channel, job=_job(), summary="r", html="<html/>", mention="<@123>")
    )
    assert out.used_fallback is True
    assert len(channel.sent) == 1
    assert FALLBACK_NOTICE in channel.sent[0][0]
    assert channel.sent[0][1] is None


def test_fallback_never_leaks_a_local_server_path(tmp_path: Path) -> None:
    path = persist_report(tmp_path, "job-1", "<html/>")
    channel = _Channel(fail_with_file=_forbidden())
    asyncio.run(
        send_report(channel, job=_job(str(path)), summary="r", html="<html/>", mention="<@123>")
    )
    assert str(tmp_path) not in channel.sent[0][0]
    assert "reports" not in channel.sent[0][0]


def test_a_failing_fallback_is_also_contained() -> None:
    channel = _Channel(fail_always=_forbidden())
    out = asyncio.run(
        send_report(channel, job=_job(), summary="r", html="<html/>", mention="<@123>")
    )
    assert out.status == "failed"
    assert channel.sent == []


def test_http_exception_on_attachment_is_contained_without_fallback() -> None:
    channel = _Channel(fail_with_file=_http_error(500))
    out = asyncio.run(
        send_report(channel, job=_job(), summary="r", html="<html/>", mention="<@123>")
    )
    assert out.status == "failed"
    assert out.http_status == 500
    assert out.used_fallback is False


def test_send_text_contains_http_errors_too() -> None:
    channel = _Channel(fail_always=_http_error(503))
    out = asyncio.run(send_text(channel, job=_job(), content="oi"))
    assert out.status == "failed"
    assert out.http_status == 503


def test_redelivery_reads_the_persisted_report(tmp_path: Path) -> None:
    path = persist_report(tmp_path, "job-1", "<html>persistido</html>")
    channel = _Channel()
    out = asyncio.run(deliver_existing_report(channel, job=_job(str(path))))
    assert out.delivered
    assert channel.sent[0][1].filename == "relatorio.html"


def test_redelivery_makes_zero_wcl_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RC.10: reenvio != reanálise."""
    import httpx

    def explode(*_a: object, **_k: object) -> None:
        pytest.fail("redelivery must never touch the network")

    monkeypatch.setattr(httpx.Client, "send", explode)
    monkeypatch.setattr(httpx.Client, "request", explode)
    path = persist_report(tmp_path, "job-1", "<html/>")
    out = asyncio.run(deliver_existing_report(_Channel(), job=_job(str(path))))
    assert out.delivered


def test_redelivery_without_a_persisted_report_fails_cleanly(tmp_path: Path) -> None:
    assert asyncio.run(deliver_existing_report(_Channel(), job=_job(None))).status == "failed"
    missing = str(tmp_path / "sumiu.html")
    assert asyncio.run(deliver_existing_report(_Channel(), job=_job(missing))).status == "failed"
