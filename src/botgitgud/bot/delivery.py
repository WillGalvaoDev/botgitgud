"""RC.3/RC.9/RC.10/CL.5 — fronteira de entrega no Discord.

Incidente 1 (docs/operations.md): `channel.send(...,
file=...)` levantou `discord.Forbidden` 403/50013, a excecao subiu por
`_notify_outcome` e `_worker_loop` e matou a task do worker.

Incidente 2 (mesmo documento, secao "Segundo smoke"): o caminho interativo
tinha uma implementacao separada e menos resiliente — nao persistia o relatorio
e ainda assim dizia ao usuario que o resultado fora preservado.

Incidente 3 (soak real, pos-EB.6/RP.3): anexos geravam preview de texto cru.
Nenhuma função deste módulo anexa arquivo; `test_delivery.py` prova isso por
AST toda vez que os testes rodam.

Regras desta camada:

1. **Nenhuma excecao da API do Discord atravessa daqui para cima.** Toda falha
   vira um `DeliveryOutcome` explicito, classificado e logado.
2. **Falha de render na análise é explícita** e nunca rebaixada para anexo.
3. **Todo evento carrega o contexto do canal** (`DeliveryContext`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import discord
import structlog

from botgitgud.bot.job_models import DeliveryStatus, Job
from botgitgud.report.coaching_answer import (
    MAX_DISCORD_COACHING_ANSWER,
    render_coaching_answer,
)
from botgitgud.report.contract import ReportContract

log = structlog.get_logger(__name__)


class Sendable(Protocol):
    """O mínimo que esta camada precisa de um canal do Discord."""

    async def send(self, content: str, *, allowed_mentions: Any | None = ...) -> Any: ...


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


async def send_text(
    channel: Sendable, *, content: str, context: DeliveryContext
) -> DeliveryOutcome:
    """Mensagem sem link/anexo (falha de dominio, build_cohort, aviso)."""
    log.info("delivery.started", **context.fields())
    try:
        await channel.send(content)
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


async def _send_one_message(
    channel: Sendable, content: str, *, artifact_id: str, context: DeliveryContext
) -> DeliveryOutcome:
    """O ÚNICO `channel.send` de uma entrega de relatório bem-sucedida —
    sempre com `allowed_mentions=AllowedMentions.none()` (obrigatório no
    CL.5: a sanitização textual é defesa em profundidade, não substituto).
    Falha aqui usa a semântica de retry já existente.
    """
    log.info("report_delivery.started", artifact_id=artifact_id, **context.fields())
    try:
        await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
    except discord.Forbidden as e:
        message, status, code = _describe(e)
        log.warning(
            "report_delivery.send_failed",
            reason="forbidden",
            artifact_id=artifact_id,
            http_status=status,
            discord_code=code,
            **context.fields(),
        )
        return DeliveryOutcome("failed", error=message, http_status=status, discord_code=code)
    except discord.HTTPException as e:
        message, status, code = _describe(e)
        log.warning(
            "report_delivery.send_failed",
            reason="http_exception",
            artifact_id=artifact_id,
            http_status=status,
            discord_code=code,
            **context.fields(),
        )
        return DeliveryOutcome("failed", error=message, http_status=status, discord_code=code)

    log.info("report_delivery.sent", artifact_id=artifact_id, **context.fields())
    return DeliveryOutcome("delivered")


async def deliver_completed_report(
    channel: Sendable,
    *,
    contract: ReportContract,
    artifact_id: str,
    context: DeliveryContext,
) -> DeliveryOutcome:
    """Entrega uma resposta de coaching em uma mensagem, sem anexo ou URL."""
    try:
        content = render_coaching_answer(contract)
    except Exception as e:
        log.error(
            "report_delivery.render_failed",
            artifact_id=artifact_id,
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True,
            **context.fields(),
        )
        return DeliveryOutcome(
            "failed", error=f"falha ao renderizar resposta de coaching: {type(e).__name__}"
        )

    # O renderizador prova o teto por orçamento de campo; esta checagem na
    # fronteira impede que uma regressão futura chegue ao Discord.
    assert len(content) <= MAX_DISCORD_COACHING_ANSWER

    return await _send_one_message(channel, content, artifact_id=artifact_id, context=context)
