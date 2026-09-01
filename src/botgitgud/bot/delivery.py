"""RC.3/RC.9/RC.10/CL.5 — fronteira de entrega no Discord.

Incidente 1 (docs/rc-discord-delivery-resilience.md): `channel.send(...,
file=...)` levantou `discord.Forbidden` 403/50013, a excecao subiu por
`_notify_outcome` e `_worker_loop` e matou a task do worker.

Incidente 2 (mesmo documento, secao "Segundo smoke"): o caminho interativo
tinha uma implementacao separada e menos resiliente — nao persistia o relatorio
e ainda assim dizia ao usuario que o resultado fora preservado.

Incidente 3 (soak real, pos-EB.6/RP.3): o Discord passou a fazer PREVIEW do
`.html` anexado como texto puro (discord.py 2.7.1's `discord.File` nao expõe
`content_type`; o Discord infere pela extensão e mostra o markup cru). CL.5
elimina ESTRUTURALMENTE esse vetor: nenhuma função deste módulo anexa
arquivo — `test_delivery.py` prova isso por AST toda vez que os testes
rodam, nunca só por revisão manual.

O produto agora é: HTML persistido (report_store.py) -> capability link
(report_links.py, CL.2) -> resumo compacto (report/discord_summary.py, CL.4)
+ link -> UMA mensagem. `bot/report_server.py` (CL.3) serve o HTML atrás do
link; nada aqui conhece HTTP.

Regras desta camada:

1. **Nenhuma excecao da API do Discord atravessa daqui para cima.** Toda falha
   vira um `DeliveryOutcome` explicito, classificado e logado.
2. **Falha de link/URL/render é SEMPRE terminal e explícita** (política do
   ticket CL.5) — nunca mascarada como "relatório indisponível" e nunca
   rebaixada para um attachment. Só uma falha do PRÓPRIO envio ao Discord
   usa a semântica de retry já existente (a capability persistida sobrevive
   ao retry — CL.2 já é idempotente por artifact_id).
3. **Todo evento carrega o contexto do canal** (`DeliveryContext`) e NUNCA o
   token/URL completos — só `artifact_id`/`token_fingerprint` (ver
   `bot/report_links.py::_fingerprint`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import discord
import structlog

from botgitgud.bot.job_models import DeliveryStatus, Job
from botgitgud.bot.report_links import (
    ReportLinkError,
    ReportLinkStore,
    _fingerprint,
    issue_report_link,
)
from botgitgud.bot.report_store import ReportPersistenceError
from botgitgud.bot.report_url import ReportPublicBaseUrlError, build_report_url
from botgitgud.report.contract import ReportContract
from botgitgud.report.discord_summary import MAX_DISCORD_REPORT_SUMMARY, render_discord_summary

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


@dataclass(frozen=True, slots=True)
class ReportDeliveryConfig:
    """CL.5 — o que a entrega de relatório precisa para ir de `artifact_id`
    a URL pública. `link_store` embrulha a MESMA `Store` (uma conexão
    DuckDB por processo) que o resto do bot usa — nunca uma segunda
    conexão criada aqui. `public_base_url` já vem validada
    (`bot/report_url.py`) no momento em que o bot sobe (`build_bot`),
    então uma entrega individual nunca descobre uma configuração inválida
    tarde demais.
    """

    link_store: ReportLinkStore
    data_dir: Path
    public_base_url: str


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


def _issue_link_url(
    config: ReportDeliveryConfig, *, artifact_id: str, context: DeliveryContext
) -> tuple[str, None] | tuple[None, DeliveryOutcome]:
    """Passos A+B da política de falha do CL.5: emitir/reusar a capability
    (CL.2, idempotente por `artifact_id`) e construir a URL pública. Uma
    falha em qualquer um dos dois é SEMPRE terminal — nunca mascarada como
    "relatório indisponível", nunca some para um attachment.
    """
    try:
        link = issue_report_link(
            config.link_store, data_dir=config.data_dir, artifact_id=artifact_id
        )
    except (ReportPersistenceError, ReportLinkError) as e:
        log.error(
            "report_delivery.link_issue_failed",
            artifact_id=artifact_id,
            error=str(e),
            error_type=type(e).__name__,
            **context.fields(),
        )
        return None, DeliveryOutcome(
            "failed", error=f"falha ao emitir link do relatório: {type(e).__name__}"
        )

    log.info(
        "report_delivery.link_ready",
        artifact_id=artifact_id,
        token_fingerprint=_fingerprint(link.token),
        **context.fields(),
    )

    try:
        report_url = build_report_url(config.public_base_url, link.token)
    except ReportPublicBaseUrlError as e:
        log.error(
            "report_delivery.url_build_failed",
            artifact_id=artifact_id,
            error=str(e),
            **context.fields(),
        )
        return None, DeliveryOutcome(
            "failed", error=f"falha ao construir URL do relatório: {type(e).__name__}"
        )

    return report_url, None


async def _send_one_message(
    channel: Sendable, content: str, *, artifact_id: str, context: DeliveryContext
) -> DeliveryOutcome:
    """O ÚNICO `channel.send` de uma entrega de relatório bem-sucedida —
    sempre com `allowed_mentions=AllowedMentions.none()` (obrigatório no
    CL.5: a sanitização textual de CL.4 é defesa em profundidade, não
    substituto). Falha aqui usa a semântica de retry já existente: a
    capability já foi persistida antes desta chamada, então um retry
    reusa a MESMA URL (CL.2).
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
    config: ReportDeliveryConfig,
    context: DeliveryContext,
) -> DeliveryOutcome:
    """CL.5 — o caminho único de entrega para uma análise concluída. HTML
    JÁ persistido é pré-condição do chamador (worker.py/discord_bot.py
    persistem antes de chamar isto, como já faziam desde RC.1/A.3).

    Ordem: emitir/reusar link -> montar URL -> renderizar resumo -> UM
    `channel.send`. Falha em qualquer um dos três primeiros passos é
    terminal e explícita (política A/B/C do ticket); só a falha do send
    em si (D) usa retry.
    """
    report_url, failure = _issue_link_url(config, artifact_id=artifact_id, context=context)
    if failure is not None:
        return failure
    assert report_url is not None

    try:
        content = render_discord_summary(contract, report_url=report_url)
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
            "failed", error=f"falha ao renderizar resumo compacto: {type(e).__name__}"
        )

    # CL.4 já prova isto por teste de propriedade; cinto-e-suspensório
    # aqui garante que uma regressão futura em discord_summary.py nunca
    # chega ao Discord sem ser notada.
    assert len(content) <= MAX_DISCORD_REPORT_SUMMARY

    return await _send_one_message(channel, content, artifact_id=artifact_id, context=context)


_REDELIVERY_NOTICE_DEFAULT = "🔄 Reenvio do relatório desta análise."


async def deliver_report_link(
    channel: Sendable,
    *,
    artifact_id: str,
    config: ReportDeliveryConfig,
    context: DeliveryContext,
    notice: str | None = None,
) -> DeliveryOutcome:
    """RC.10 — reenvio de um relatório JÁ persistido, sem reanálise. Ao
    contrário de `deliver_completed_report`, não recebe (nem reconstrói)
    um `ReportContract` — o texto renderizado nunca é persistido à parte
    do HTML, então um reenvio puro reemite/reusa a MESMA capability (CL.2
    é idempotente por `artifact_id`) e reenvia o MESMO link que o usuário
    já teria recebido, com um aviso curto no lugar do resumo completo.
    `issue_report_link` já falha alto (`ReportArtifactNotFoundError`,
    subclasse de `ReportPersistenceError`) se o `.html` não existir mais.
    """
    report_url, failure = _issue_link_url(config, artifact_id=artifact_id, context=context)
    if failure is not None:
        return failure
    assert report_url is not None

    text = notice or _REDELIVERY_NOTICE_DEFAULT
    content = f"{text}\n🔗 [Ver relatório completo]({report_url})"
    return await _send_one_message(channel, content, artifact_id=artifact_id, context=context)


async def deliver_existing_report(
    channel: Sendable,
    *,
    job: Job,
    config: ReportDeliveryConfig,
    notice: str | None = None,
) -> DeliveryOutcome:
    """Reenvio a partir de um job da fila — `job.job_id` É o `artifact_id`
    (mesma identidade que `worker.py` usa para persistir, ver
    `report_path_for`).
    """
    if not job.report_path:
        return DeliveryOutcome("failed", error="job sem report_path persistido")
    return await deliver_report_link(
        channel,
        artifact_id=job.job_id,
        config=config,
        context=DeliveryContext.from_job(job),
        notice=notice,
    )
