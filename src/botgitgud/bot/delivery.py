"""RC.3/RC.9/RC.10 — fronteira de entrega no Discord.

Incidente 1 (docs/rc-discord-delivery-resilience.md): `channel.send(...,
file=...)` levantou `discord.Forbidden` 403/50013, a excecao subiu por
`_notify_outcome` e `_worker_loop` e matou a task do worker.

Incidente 2 (mesmo documento, secao "Segundo smoke"): o caminho interativo
tinha uma implementacao separada e menos resiliente — nao persistia o relatorio
e ainda assim dizia ao usuario que o resultado fora preservado.

Regras desta camada:

1. **Nenhuma excecao da API do Discord atravessa daqui para cima.** Toda falha
   vira um `DeliveryOutcome` explicito, classificado e logado. Isso nao e
   `except Exception: pass` — erros ficam visiveis nos logs.
2. **A camada nunca decide se o artefato foi preservado.** Quem chama informa
   (`preserved`), porque so quem chama sabe se a persistencia deu certo. Uma
   mensagem de fallback nunca pode afirmar preservacao que nao aconteceu.
3. **Todo evento carrega o contexto do canal** (`DeliveryContext`), para que um
   403/50013 identifique exatamente qual canal esta sem permissao.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import discord
import structlog

from botgitgud.bot.job_models import DeliveryStatus, Job
from botgitgud.bot.report_store import ReportPersistenceError, load_report

log = structlog.get_logger(__name__)

REPORT_FILENAME = "relatorio.html"

# RC.9/A.4: dois textos distintos, porque a verdade e distinta. Nenhum deles
# revela caminho local do servidor.
PRESERVED_NOTICE = (
    "⚠️ A análise foi concluída e o relatório foi preservado, mas não consegui "
    "anexá-lo neste canal. Ele pode ser reenviado."
)
NOT_PRESERVED_NOTICE = (
    "⚠️ A análise foi concluída, mas não consegui anexar o relatório neste canal "
    "e também não foi possível guardá-lo. Refaça a análise para obtê-lo."
)


class Sendable(Protocol):
    """O mínimo que esta camada precisa de um canal do Discord."""

    async def send(self, content: str, *, file: Any | None = ...) -> Any: ...


class ChannelResolver(Protocol):
    """O mínimo que a notificação precisa do bot: resolver um canal. Tipar pelo
    uso real mantém os fakes de teste válidos sem `type: ignore`.
    """

    def get_channel(self, channel_id: int, /) -> Any: ...


@dataclass(frozen=True, slots=True)
class DeliveryContext:
    """B.1 — contexto estruturado de toda tentativa de entrega. Sem token, sem
    conteúdo de relatório, sem nome de jogador.
    """

    channel_id: str | None = None
    guild_id: str | None = None
    job_id: str | None = None
    correlation_id: str | None = None

    @classmethod
    def from_job(cls, job: Job) -> DeliveryContext:
        return cls(channel_id=job.discord_channel_id, job_id=job.job_id)

    def fields(self) -> dict[str, str | None]:
        return {
            "channel_id": self.channel_id,
            "guild_id": self.guild_id,
            "job_id": self.job_id,
            "correlation_id": self.correlation_id,
        }


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    status: DeliveryStatus
    error: str | None = None
    http_status: int | None = None
    discord_code: int | None = None
    used_fallback: bool = False

    @property
    def delivered(self) -> bool:
        return self.status == "delivered"


def context_from_discord(channel: Any, *, job_id: str | None = None) -> DeliveryContext:
    """Extrai ids do objeto do Discord sem exigir um tipo concreto — funciona
    para `Context` (interativo) e para um canal resolvido (fila).
    """
    channel_id = getattr(getattr(channel, "channel", None), "id", None) or getattr(
        channel, "id", None
    )
    guild = getattr(channel, "guild", None)
    return DeliveryContext(
        channel_id=None if channel_id is None else str(channel_id),
        guild_id=None if guild is None else str(getattr(guild, "id", None)),
        job_id=job_id,
    )


def _describe(e: discord.HTTPException) -> tuple[str, int | None, int | None]:
    status = getattr(e, "status", None)
    code = getattr(e, "code", None)
    return (f"discord http {status} code {code}: {e.text or type(e).__name__}", status, code)


async def _send(channel: Sendable, content: str, *, file: Any | None = None) -> None:
    if file is None:
        await channel.send(content)
    else:
        await channel.send(content, file=file)


def _as_stream(html: str) -> io.BytesIO:
    return io.BytesIO(html.encode("utf-8"))


async def send_report(
    channel: Sendable,
    *,
    summary: str,
    html: str,
    mention: str = "",
    context: DeliveryContext,
    preserved: bool,
) -> DeliveryOutcome:
    """Envia resumo + anexo HTML. Caminho unico para interativo e fila.

    `preserved` diz se o artefato ja esta em disco; ele so decide QUAL texto de
    fallback e honesto, nunca se o envio acontece.
    """
    prefix = f"{mention}\n" if mention else ""
    body = f"{prefix}```markdown\n{summary}\n```"
    log.info("delivery.started", **context.fields())
    try:
        await _send(channel, body, file=discord.File(_as_stream(html), filename=REPORT_FILENAME))
    except discord.Forbidden as e:
        message, status, code = _describe(e)
        log.warning(
            "delivery.failed",
            reason="forbidden",
            http_status=status,
            discord_code=code,
            report_preserved=preserved,
            **context.fields(),
        )
        return await _try_fallback(
            channel,
            context=context,
            mention=mention,
            cause=message,
            http_status=status,
            code=code,
            preserved=preserved,
        )
    except discord.HTTPException as e:
        message, status, code = _describe(e)
        log.warning(
            "delivery.failed",
            reason="http_exception",
            http_status=status,
            discord_code=code,
            report_preserved=preserved,
            **context.fields(),
        )
        return DeliveryOutcome("failed", error=message, http_status=status, discord_code=code)
    log.info("delivery.succeeded", **context.fields())
    return DeliveryOutcome("delivered")


async def _try_fallback(
    channel: Sendable,
    *,
    context: DeliveryContext,
    mention: str,
    cause: str,
    http_status: int | None,
    code: int | None,
    preserved: bool,
) -> DeliveryOutcome:
    notice = PRESERVED_NOTICE if preserved else NOT_PRESERVED_NOTICE
    text = f"{mention} {notice}" if mention else notice
    try:
        await _send(channel, text)
    except discord.HTTPException as e:
        message, status, fallback_code = _describe(e)
        log.warning(
            "delivery.fallback_failed",
            http_status=status,
            discord_code=fallback_code,
            **context.fields(),
        )
        return DeliveryOutcome(
            "failed",
            error=f"{cause}; fallback: {message}",
            http_status=http_status,
            discord_code=code,
        )
    log.info("delivery.fallback_succeeded", report_preserved=preserved, **context.fields())
    # O artefato nao foi entregue: a entrega continua `failed`, mesmo que o
    # aviso curto tenha chegado.
    return DeliveryOutcome(
        "failed", error=cause, http_status=http_status, discord_code=code, used_fallback=True
    )


async def send_text(
    channel: Sendable, *, content: str, context: DeliveryContext
) -> DeliveryOutcome:
    """Mensagem sem anexo (falha de dominio, build_cohort, aviso)."""
    log.info("delivery.started", **context.fields())
    try:
        await _send(channel, content)
    except discord.HTTPException as e:
        message, status, code = _describe(e)
        log.warning(
            "delivery.failed",
            reason="http_exception",
            http_status=status,
            discord_code=code,
            **context.fields(),
        )
        return DeliveryOutcome("failed", error=message, http_status=status, discord_code=code)
    log.info("delivery.succeeded", **context.fields())
    return DeliveryOutcome("delivered")


async def deliver_persisted_report(
    channel: Sendable,
    *,
    report_path: str | Path,
    context: DeliveryContext,
    mention: str = "",
    summary: str | None = None,
) -> DeliveryOutcome:
    """RC.10/A.6 — reenvia um relatorio JA persistido, de qualquer caminho.
    Redelivery != reanalysis: le do disco e **nao faz nenhuma chamada a WCL**.
    """
    try:
        html = load_report(report_path)
    except ReportPersistenceError as e:
        log.warning("delivery.report_unreadable", error=str(e), **context.fields())
        return DeliveryOutcome("failed", error=str(e))
    return await send_report(
        channel,
        summary=summary or "Reenvio do relatório desta análise.",
        html=html,
        mention=mention,
        context=context,
        preserved=True,
    )


async def deliver_existing_report(
    channel: Sendable, *, job: Job, summary: str | None = None
) -> DeliveryOutcome:
    """Reenvio a partir de um job da fila (mantem a assinatura do RC anterior)."""
    if not job.report_path:
        return DeliveryOutcome("failed", error="job sem report_path persistido")
    return await deliver_persisted_report(
        channel,
        report_path=job.report_path,
        context=DeliveryContext.from_job(job),
        mention=f"<@{job.discord_user_id}>",
        summary=summary,
    )
