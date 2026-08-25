"""RC.3/RC.9/RC.10 — fronteira de entrega no Discord.

Incidente real (docs/rc-discord-delivery-resilience.md): `channel.send(...,
file=...)` levantou `discord.Forbidden` 403/50013 (Missing Permissions), a
exceção subiu por `_notify_outcome` e por `_worker_loop`, e matou a task do
worker. O bot continuou online, sem consumidor de fila, e o relatório — que só
existia em memória — se perdeu.

Regra desta camada: **nenhuma exceção da API do Discord atravessa daqui para
cima**. Toda falha vira um `DeliveryOutcome` explícito, classificado, logado e
persistido pelo chamador. Isso não é `except Exception: pass` — erros
inesperados continuam sendo logados com stack e classificados como `unexpected`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import discord
import structlog

from botgitgud.bot.job_models import DeliveryStatus, Job
from botgitgud.bot.report_store import ReportPersistenceError, load_report

log = structlog.get_logger(__name__)

REPORT_FILENAME = "relatorio.html"

# RC.9: o texto do fallback nunca revela caminho local do servidor.
FALLBACK_NOTICE = (
    "⚠️ A análise foi concluída, mas não consegui anexar o relatório neste canal. "
    "O resultado foi preservado para reenvio."
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
class DeliveryOutcome:
    status: DeliveryStatus
    error: str | None = None
    http_status: int | None = None
    discord_code: int | None = None
    used_fallback: bool = False

    @property
    def delivered(self) -> bool:
        return self.status == "delivered"


def _describe(e: discord.HTTPException) -> tuple[str, int | None, int | None]:
    status = getattr(e, "status", None)
    code = getattr(e, "code", None)
    return (f"discord http {status} code {code}: {e.text or type(e).__name__}", status, code)


async def _send(channel: Sendable, content: str, *, file: Any | None = None) -> None:
    if file is None:
        await channel.send(content)
    else:
        await channel.send(content, file=file)


async def send_report(
    channel: Sendable,
    *,
    job: Job,
    summary: str,
    html: str,
    mention: str,
) -> DeliveryOutcome:
    """Envia resumo + anexo HTML. Em Forbidden tenta o fallback textual curto
    (RC.9); se o fallback também falhar, registra a falha e retorna — a segunda
    falha nunca escapa (RC.9 in fine).
    """
    body = f"{mention}\n```markdown\n{summary}\n```"
    log.info("delivery.started", job_id=job.job_id, channel_id=job.discord_channel_id)
    try:
        await _send(
            channel,
            body,
            file=discord.File(_as_stream(html), filename=REPORT_FILENAME),
        )
    except discord.Forbidden as e:
        message, status, code = _describe(e)
        log.warning(
            "delivery.failed",
            job_id=job.job_id,
            channel_id=job.discord_channel_id,
            http_status=status,
            discord_code=code,
            reason="forbidden",
        )
        return await _try_fallback(
            channel, job=job, mention=mention, cause=message, http_status=status, code=code
        )
    except discord.HTTPException as e:
        message, status, code = _describe(e)
        log.warning(
            "delivery.failed",
            job_id=job.job_id,
            channel_id=job.discord_channel_id,
            http_status=status,
            discord_code=code,
            reason="http_exception",
        )
        return DeliveryOutcome("failed", error=message, http_status=status, discord_code=code)
    log.info("delivery.succeeded", job_id=job.job_id, channel_id=job.discord_channel_id)
    return DeliveryOutcome("delivered")


async def _try_fallback(
    channel: Sendable,
    *,
    job: Job,
    mention: str,
    cause: str,
    http_status: int | None,
    code: int | None,
) -> DeliveryOutcome:
    try:
        await _send(channel, f"{mention} {FALLBACK_NOTICE}")
    except discord.HTTPException as e:
        message, status, code = _describe(e)
        log.warning(
            "delivery.fallback_failed",
            job_id=job.job_id,
            channel_id=job.discord_channel_id,
            http_status=status,
            discord_code=code,
        )
        return DeliveryOutcome(
            "failed",
            error=f"{cause}; fallback: {message}",
            http_status=http_status,
            discord_code=code,
        )
    log.info("delivery.fallback_succeeded", job_id=job.job_id)
    # A entrega do artefato falhou: o relatorio segue pendente de reenvio.
    return DeliveryOutcome(
        "failed", error=cause, http_status=http_status, discord_code=code, used_fallback=True
    )


async def send_text(channel: Sendable, *, job: Job, content: str) -> DeliveryOutcome:
    """Mensagem sem anexo (falha de dominio, build_cohort, aviso)."""
    log.info("delivery.started", job_id=job.job_id, channel_id=job.discord_channel_id)
    try:
        await _send(channel, content)
    except discord.HTTPException as e:
        message, status, code = _describe(e)
        log.warning(
            "delivery.failed",
            job_id=job.job_id,
            channel_id=job.discord_channel_id,
            http_status=status,
            discord_code=code,
            reason="http_exception",
        )
        return DeliveryOutcome("failed", error=message, http_status=status, discord_code=code)
    log.info("delivery.succeeded", job_id=job.job_id, channel_id=job.discord_channel_id)
    return DeliveryOutcome("delivered")


async def send_interactive_report(channel: Sendable, *, summary: str, html: str) -> DeliveryOutcome:
    """Caminho interativo (`!analisar` com coorte quente): mesmo contrato de
    entrega da fila, mesma protecao. Sem job por tras, entao nada e persistido
    aqui — mas um 403 responde ao usuario em vez de estourar no handler.
    """
    body = "```markdown" + chr(10) + summary + chr(10) + "```"
    try:
        await _send(channel, body, file=discord.File(_as_stream(html), filename=REPORT_FILENAME))
    except discord.Forbidden as e:
        message, status, code = _describe(e)
        log.warning("delivery.interactive_forbidden", http_status=status, discord_code=code)
        try:
            await _send(channel, FALLBACK_NOTICE)
        except discord.HTTPException:
            log.warning("delivery.interactive_fallback_failed")
        return DeliveryOutcome("failed", error=message, http_status=status, discord_code=code)
    except discord.HTTPException as e:
        message, status, code = _describe(e)
        log.warning("delivery.interactive_failed", http_status=status, discord_code=code)
        return DeliveryOutcome("failed", error=message, http_status=status, discord_code=code)
    return DeliveryOutcome("delivered")


async def deliver_existing_report(
    channel: Sendable, *, job: Job, summary: str | None = None
) -> DeliveryOutcome:
    """RC.10 — reenvia um relatório JÁ persistido. Redelivery != reanalysis:
    lê do disco e **não faz nenhuma chamada à WCL**.
    """
    if not job.report_path:
        return DeliveryOutcome("failed", error="job sem report_path persistido")
    try:
        html = load_report(job.report_path)
    except ReportPersistenceError as e:
        log.warning("delivery.report_unreadable", job_id=job.job_id, error=str(e))
        return DeliveryOutcome("failed", error=str(e))
    return await send_report(
        channel,
        job=job,
        summary=summary or "Reenvio do relatório desta análise.",
        html=html,
        mention=f"<@{job.discord_user_id}>",
    )


def _as_stream(html: str) -> Any:
    import io

    return io.BytesIO(html.encode("utf-8"))
