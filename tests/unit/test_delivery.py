"""RC.3/RC.8/RC.9/RC.10/CL.5 — fronteira de entrega.

Regressão do incidente real (3x): 403/50013 no attachment (RC), caminho
interativo menos resiliente (RC), e o Discord fazendo PREVIEW do `.html`
anexado como texto puro num soak real (CL.5 — o motivo de delivery.py não
anexar mais nenhum arquivo). Nada aqui toca o Discord ou WCL de verdade.
"""

from __future__ import annotations

import asyncio
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import discord
import pytest
import structlog

from botgitgud.bot.delivery import (
    DeliveryContext,
    ReportDeliveryConfig,
    deliver_completed_report,
    deliver_existing_report,
    send_text,
)
from botgitgud.bot.job_models import Job, now_utc_naive
from botgitgud.bot.report_links import ReportLinkStore
from botgitgud.bot.report_store import persist_report
from botgitgud.ingest.store import Store
from botgitgud.report.contract import ConfidenceSummary, ExecutionSection, ReportContract
from botgitgud.report.discord_summary import MAX_DISCORD_REPORT_SUMMARY
from botgitgud.report.text import ReportHeader

_ARTIFACT_ID = "job-1"
_HTML = "<html><body>relatorio</body></html>"


def _header(**overrides: object) -> ReportHeader:
    defaults: dict[str, object] = {
        "char_name": "Zarad",
        "boss_name": "Fallen-King Salhadaar",
        "class_name": "Warlock",
        "spec": "Demonology",
        "reference_n": 20,
        "duration_min_s": 300.0,
        "duration_max_s": 360.0,
        "player_dps": 108297.0,
        "player_percentile": 57.0,
    }
    defaults.update(overrides)
    return ReportHeader(**defaults)  # type: ignore[arg-type]


def _contract(**overrides: object) -> ReportContract:
    defaults: dict[str, object] = {
        "resultado": _header(),
        "setup": None,
        "execucao": ExecutionSection(comparisons=(), performance=None, dps_gap=None),
        "top_actions": (),
        "confianca": ConfidenceSummary(
            reference_pool_members=40,
            matched_cohort_members=20,
            cohort_warnings=(),
            matched_covariates=(),
            relaxed_covariates=(),
        ),
        "manifest": None,
    }
    defaults.update(overrides)
    return ReportContract(**defaults)  # type: ignore[arg-type]


def _job(report_path: str | None = None) -> Job:
    return Job(
        job_id=_ARTIFACT_ID,
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


def _ctx() -> DeliveryContext:
    return DeliveryContext(channel_id="456", guild_id="789", job_id=_ARTIFACT_ID)


def _config(
    tmp_path: Path, *, base_url: str = "https://botgitgud.duckdns.org"
) -> ReportDeliveryConfig:
    store = Store(tmp_path)
    return ReportDeliveryConfig(
        link_store=ReportLinkStore(store), data_dir=tmp_path, public_base_url=base_url
    )


def _forbidden(code: int = 50013) -> discord.Forbidden:
    response: Any = type("R", (), {"status": 403, "reason": "Forbidden"})()
    return discord.Forbidden(response, {"code": code, "message": "Missing Permissions"})


def _http_error(status: int = 500, code: int = 0) -> discord.HTTPException:
    response: Any = type("R", (), {"status": status, "reason": "Server Error"})()
    return discord.HTTPException(response, {"code": code, "message": "boom"})


class _Channel:
    """Captura content + TODOS os kwargs — nunca só `file`, para nunca deixar
    passar despercebido um `files=`/`attachments=` que a assinatura antiga
    não previa.
    """

    def __init__(self, *, fail: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._fail = fail

    async def send(self, content: str, **kwargs: Any) -> Any:
        if self._fail is not None:
            raise self._fail
        self.calls.append((content, kwargs))


def _deliver(tmp_path: Path, *, channel: Any, config: ReportDeliveryConfig | None = None) -> Any:
    persist_report(tmp_path, _ARTIFACT_ID, _HTML)
    cfg = config or _config(tmp_path)
    return asyncio.run(
        deliver_completed_report(
            channel, contract=_contract(), artifact_id=_ARTIFACT_ID, config=cfg, context=_ctx()
        )
    )


# -- 14/15/16/17/18: happy path — one message, contract-driven, <=1800, url, allowed_mentions


def test_normal_delivery_sends_exactly_one_compact_message(tmp_path: Path) -> None:
    channel = _Channel()
    out = _deliver(tmp_path, channel=channel)
    assert out.delivered
    assert len(channel.calls) == 1


def test_message_content_is_within_1800_and_contains_the_url(tmp_path: Path) -> None:
    channel = _Channel()
    _deliver(tmp_path, channel=channel)
    content, _kwargs = channel.calls[0]
    assert len(content) <= MAX_DISCORD_REPORT_SUMMARY
    assert "https://botgitgud.duckdns.org/r/" in content
    assert "Zarad" in content


def test_allowed_mentions_none_is_always_passed(tmp_path: Path) -> None:
    channel = _Channel()
    _deliver(tmp_path, channel=channel)
    _content, kwargs = channel.calls[0]
    mentions = kwargs.get("allowed_mentions")
    assert isinstance(mentions, discord.AllowedMentions)
    assert mentions.to_dict() == discord.AllowedMentions.none().to_dict()


# -- 19/20/21/22: zero file/files/attachment/attachments, zero chunking ---------------------


def test_no_file_files_or_attachments_kwarg_ever_passed(tmp_path: Path) -> None:
    channel = _Channel()
    _deliver(tmp_path, channel=channel)
    _content, kwargs = channel.calls[0]
    for forbidden in ("file", "files", "attachment", "attachments"):
        assert forbidden not in kwargs


def test_never_produces_multiple_messages_or_chunks(tmp_path: Path) -> None:
    channel = _Channel()
    _deliver(tmp_path, channel=channel)
    assert len(channel.calls) == 1  # never a summary message + a follow-up


# -- failure policy A/B/C: link/url/render failures are terminal, never masked --------------


def test_missing_persisted_artifact_is_a_terminal_failure_not_masked(tmp_path: Path) -> None:
    """A: issue_report_link falha (artefato nunca foi persistido) — delivery
    falha explicitamente, NUNCA envia 'relatório temporariamente
    indisponível' como se tudo tivesse concluído.
    """
    channel = _Channel()
    config = _config(tmp_path)
    out = asyncio.run(
        deliver_completed_report(
            channel,
            contract=_contract(),
            artifact_id="never-persisted",
            config=config,
            context=_ctx(),
        )
    )
    assert out.status == "failed"
    assert channel.calls == []


def test_invalid_public_base_url_is_a_terminal_failure(tmp_path: Path) -> None:
    """B: base_url inválida (aqui, um esquema proibido colado manualmente
    após a validação normal de build_bot — simula um caminho de teste que
    contorna essa validação) — delivery falha, nunca envia nada.
    """
    persist_report(tmp_path, _ARTIFACT_ID, _HTML)
    channel = _Channel()
    store = Store(tmp_path)
    # Contorna validate_report_public_base_url deliberadamente: prova que
    # deliver_completed_report TAMBÉM falha alto se uma base inválida
    # chegasse aqui por algum outro caminho, não só build_bot.
    bad_config = ReportDeliveryConfig(
        link_store=ReportLinkStore(store), data_dir=tmp_path, public_base_url="javascript:alert(1)"
    )
    out = asyncio.run(
        deliver_completed_report(
            channel,
            contract=_contract(),
            artifact_id=_ARTIFACT_ID,
            config=bad_config,
            context=_ctx(),
        )
    )
    assert out.status == "failed"
    assert channel.calls == []


def test_renderer_failure_is_a_terminal_failure_never_masked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C: render_discord_summary levanta — delivery falha, nunca cai para o
    fallback 'indisponível' como se a análise tivesse simplesmente
    terminado sem link.
    """
    import botgitgud.bot.delivery as delivery_module

    def explode(*_a: object, **_k: object) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(delivery_module, "render_discord_summary", explode)
    channel = _Channel()
    out = _deliver(tmp_path, channel=channel)
    assert out.status == "failed"
    assert channel.calls == []


# -- D: Discord send failure uses existing retry semantics — capability survives ------------


def test_discord_send_failure_is_contained_and_reported(tmp_path: Path) -> None:
    channel = _Channel(fail=_forbidden())
    out = _deliver(tmp_path, channel=channel)
    assert out.status == "failed"
    assert out.http_status == 403
    assert out.discord_code == 50013
    assert channel.calls == []


def test_discord_http_exception_is_contained(tmp_path: Path) -> None:
    channel = _Channel(fail=_http_error(500))
    out = _deliver(tmp_path, channel=channel)
    assert out.status == "failed"
    assert out.http_status == 500


def test_capability_survives_a_failed_discord_send_for_retry(tmp_path: Path) -> None:
    """A entrega falhou no PASSO D (envio), mas o link já foi emitido no
    passo A — o retry precisa reusar a MESMA capability, nunca gerar uma
    nova nem apagar a que já existe.
    """
    config = _config(tmp_path)
    persist_report(tmp_path, _ARTIFACT_ID, _HTML)

    failing = _Channel(fail=_forbidden())
    asyncio.run(
        deliver_completed_report(
            failing, contract=_contract(), artifact_id=_ARTIFACT_ID, config=config, context=_ctx()
        )
    )
    row_count_after_failure = len(
        config.link_store._store.execute_returning(
            "SELECT token FROM report_links WHERE artifact_id = ?", [_ARTIFACT_ID]
        )
    )
    assert row_count_after_failure == 1

    succeeding = _Channel()
    out = asyncio.run(
        deliver_completed_report(
            succeeding,
            contract=_contract(),
            artifact_id=_ARTIFACT_ID,
            config=config,
            context=_ctx(),
        )
    )
    assert out.delivered
    row_count_after_retry = len(
        config.link_store._store.execute_returning(
            "SELECT token FROM report_links WHERE artifact_id = ?", [_ARTIFACT_ID]
        )
    )
    assert row_count_after_retry == 1  # nenhum token novo gerado no retry


def test_send_text_contains_http_errors_too(tmp_path: Path) -> None:
    channel = _Channel(fail=_http_error(503))
    out = asyncio.run(send_text(channel, content="oi", context=_ctx()))
    assert out.status == "failed"
    assert out.http_status == 503


# -- redelivery (RC.10): reenvio do MESMO link, sem HTML, sem reanálise ---------------------


def test_redelivery_reuses_the_same_link_and_sends_no_attachment(tmp_path: Path) -> None:
    config = _config(tmp_path)
    persist_report(tmp_path, _ARTIFACT_ID, _HTML)
    channel = _Channel()
    out = asyncio.run(
        deliver_existing_report(channel, job=_job("ignored/path.html"), config=config)
    )
    assert out.delivered
    assert len(channel.calls) == 1
    content, kwargs = channel.calls[0]
    assert "https://botgitgud.duckdns.org/r/" in content
    assert "file" not in kwargs and "files" not in kwargs


def test_redelivery_without_report_path_fails_cleanly(tmp_path: Path) -> None:
    config = _config(tmp_path)
    out = asyncio.run(deliver_existing_report(_Channel(), job=_job(None), config=config))
    assert out.status == "failed"


def test_redelivery_makes_zero_wcl_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RC.10: reenvio != reanálise."""
    import httpx

    def explode(*_a: object, **_k: object) -> None:
        pytest.fail("redelivery must never touch the network")

    monkeypatch.setattr(httpx.Client, "send", explode)
    monkeypatch.setattr(httpx.Client, "request", explode)
    config = _config(tmp_path)
    persist_report(tmp_path, _ARTIFACT_ID, _HTML)
    out = asyncio.run(
        deliver_existing_report(_Channel(), job=_job("ignored/path.html"), config=config)
    )
    assert out.delivered


# -- logging redaction: never the token, never the URL, never the message content ----------


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


def test_no_log_event_contains_the_full_token_or_url(tmp_path: Path) -> None:
    config = _config(tmp_path)
    persist_report(tmp_path, _ARTIFACT_ID, _HTML)
    channel = _Channel()

    events = _captured_events(
        lambda: asyncio.run(
            deliver_completed_report(
                channel,
                contract=_contract(),
                artifact_id=_ARTIFACT_ID,
                config=config,
                context=_ctx(),
            )
        )
    )
    assert channel.calls  # a mensagem foi de fato enviada
    sent_content = channel.calls[0][0]
    token = sent_content.rsplit("/r/", 1)[1].rstrip(")")

    for event in events:
        blob = repr(event)
        assert token not in blob, f"token completo vazou em log: {event}"
        assert "botgitgud.duckdns.org/r/" not in blob, f"URL completa vazou em log: {event}"
        assert sent_content not in blob, f"conteúdo da mensagem vazou em log: {event}"


def test_link_ready_log_carries_only_fingerprint_and_artifact_id(tmp_path: Path) -> None:
    config = _config(tmp_path)
    persist_report(tmp_path, _ARTIFACT_ID, _HTML)
    events = _captured_events(
        lambda: asyncio.run(
            deliver_completed_report(
                _Channel(),
                contract=_contract(),
                artifact_id=_ARTIFACT_ID,
                config=config,
                context=_ctx(),
            )
        )
    )
    link_ready = next(e for e in events if e.get("event") == "report_delivery.link_ready")
    assert link_ready["artifact_id"] == _ARTIFACT_ID
    assert len(link_ready["token_fingerprint"]) == 8
