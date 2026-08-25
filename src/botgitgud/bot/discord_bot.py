"""T1.6/T1.8 — Discord glue, replacing bot.py's cmd_analisar. Parses the
raw command input, tries the fast synchronous path first (T1.7's warm
cohort lookup), falls back to T1.8's persistent job queue on a cache
miss, and a background worker loop drains that queue. No analysis logic
lives here (§1.1/T1.6): every failure path translates a specific
errors.py exception into a Portuguese user-facing message, the one place
allowed to do that broad a translation (§1.3).

Like every command handler in this module, the async event-loop wiring
here is Discord glue that stays outside unit-test coverage — see
docs/desvios.md D-22. The logic it calls (run_analysis, JobQueue,
run_claimed_job) is fully unit-tested elsewhere.
"""

from __future__ import annotations

import asyncio
import functools
import re
from collections.abc import Callable, Coroutine, Sequence
from typing import Any

import discord
import structlog
from discord.ext import commands

from botgitgud.analysis.cold_build import cold_lifecycle_snapshot
from botgitgud.analysis.pipeline import AnalysisRequest, Deps, run_analysis
from botgitgud.bot.delivery import (
    ChannelResolver,
    DeliveryContext,
    context_from_discord,
    send_report,
    send_text,
)
from botgitgud.bot.job_models import BudgetStatus, EnqueueResult, Job
from botgitgud.bot.jobs import JobQueue
from botgitgud.bot.ops_snapshot import write_snapshot
from botgitgud.bot.report_store import (
    ReportPersistenceError,
    interactive_artifact_id,
    persist_report,
)
from botgitgud.bot.worker import JobOutcome, run_claimed_job
from botgitgud.errors import (
    ApiError,
    CohortNotReady,
    FightNotFound,
    InsufficientCohort,
    PlayerNotFound,
    ScopeRejected,
)
from botgitgud.report.html_report import render_html_report
from botgitgud.report.text import render_header_and_top3

log = structlog.get_logger(__name__)

_WORKER_POLL_INTERVAL_S = 2.0


def parse_report_input(input_str: str) -> tuple[str | None, int | None]:
    """Accepts a full WCL report URL (with `?fight=N`) or a bare 16-char
    report code.
    """
    fight_match = re.search(r"fight=(\d+)", input_str)
    fight_id = int(fight_match.group(1)) if fight_match else None

    code_match = re.search(r"reports/([a-zA-Z0-9]{16})", input_str)
    if code_match:
        report_code = code_match.group(1)
    else:
        clean_code = input_str.split("?")[0].split("#")[0].strip()
        report_code = clean_code if len(clean_code) == 16 else input_str

    return report_code, fight_id


def _current_budget(deps: Deps) -> BudgetStatus:
    deps.client.refresh_budget()
    remaining = deps.client.points_remaining
    limit = deps.client.points_limit
    if remaining is None or limit is None:
        return BudgetStatus(points_remaining=0.0, limit_per_hour=3600.0)
    return BudgetStatus(points_remaining=remaining, limit_per_hour=limit)


async def _notify_outcome(bot: ChannelResolver, outcome: JobOutcome, queue: JobQueue) -> None:
    """RC.3 — fronteira de entrega. Nenhuma excecao do Discord sai daqui: a
    camada bot/delivery.py classifica cada falha e devolve um DeliveryOutcome,
    que este nivel persiste sem jamais tocar no estado da analise (RC.2).
    """
    if outcome.requeued:
        # T1.8 §3: budget ran out mid-job — silently back to `queued` for
        # after pointsResetIn, no user-facing noise (it isn't done, and
        # it isn't an error the user needs to react to).
        return
    job = outcome.job
    channel = bot.get_channel(int(job.discord_channel_id))
    if channel is None:
        log.warning(
            "discord_bot.notify_channel_missing",
            job_id=job.job_id,
            channel_id=job.discord_channel_id,
        )
        queue.mark_delivery_failed(job.job_id, error="canal indisponivel")
        return

    context = context_from_discord(channel, job_id=job.job_id)
    if context.channel_id is None:
        context = DeliveryContext.from_job(job)
    mention = f"<@{job.discord_user_id}>"
    if not outcome.ok:
        result = await send_text(
            channel, content=f"{mention} ❌ {outcome.message}", context=context
        )
    elif job.job_type != "analyze":
        result = await send_text(
            channel, content=f"{mention} ✅ {outcome.message}", context=context
        )
    elif outcome.html_report is None:
        log.error("discord_bot.analyze_outcome_missing_html", job_id=job.job_id)
        result = await send_text(
            channel, content=f"{mention} ❌ relatório HTML indisponível.", context=context
        )
    else:
        result = await send_report(
            channel,
            summary=outcome.message,
            html=outcome.html_report,
            mention=mention,
            context=context,
            # A fila persiste antes de entregar: o artefato existe.
            preserved=outcome.report_path is not None,
        )

    if result.delivered:
        queue.mark_delivered(job.job_id)
    else:
        queue.mark_delivery_failed(job.job_id, error=result.error or "falha de entrega")


async def _worker_loop(bot: commands.Bot, deps: Deps, queue: JobQueue) -> None:
    last_points: float | None = None
    last_limit: float | None = None
    while True:
        await asyncio.sleep(_WORKER_POLL_INTERVAL_S)
        active = queue.list_active()
        # D-34: enquanto este processo vive, ele segura o arquivo do DuckDB e
        # nenhum outro consegue abri-lo. Publicar o resumo a cada tick é o que
        # permite ao `ops-status` responder com o bot no ar.
        _publish_snapshot(deps, queue, active, last_points, last_limit)
        if not any(j.status == "queued" for j in active):
            continue  # never spend a rate-limit check when there's nothing to run

        try:
            budget = _current_budget(deps)
        except ApiError as e:
            log.warning("discord_bot.budget_check_failed", error=str(e))
            continue
        last_points, last_limit = budget.points_remaining, budget.limit_per_hour

        job = queue.claim_next(budget)
        if job is None:
            continue

        await _run_one_job(bot, deps, queue, job)


async def _run_one_job(bot: ChannelResolver, deps: Deps, queue: JobQueue, job: Job) -> None:
    """RC.4 — ultima fronteira de isolamento: um job nunca pode matar o
    consumidor da fila. CancelledError passa direto (shutdown continua
    funcionando); qualquer outra excecao inesperada e logada com stack e
    deixa o job num estado terminal, nunca silenciosamente `running`.
    """
    try:
        loop = asyncio.get_running_loop()
        outcome = await loop.run_in_executor(None, run_claimed_job, queue, job, deps)
        await _notify_outcome(bot, outcome, queue)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("discord_bot.job_crashed", job_id=job.job_id, job_type=job.job_type)
        _fail_job_quietly(queue, job)


def _fail_job_quietly(queue: JobQueue, job: Job) -> None:
    current = queue.get(job.job_id)
    if current is not None and current.status == "running":
        queue.mark_failed(job.job_id, error="erro inesperado no processamento do job")


def _publish_snapshot(
    deps: Deps,
    queue: JobQueue,
    active: Sequence[Job],
    points_remaining: float | None,
    points_limit: float | None,
) -> None:
    """Publica o estado para o `ops-status` (D-34). Best-effort por desenho:
    falhar ao escrever um arquivo de diagnóstico nunca pode derrubar o worker.
    """
    try:
        list_recent = getattr(queue, "list_recent", lambda: active)
        store = getattr(deps, "store", None)
        ready_cohorts = [] if store is None else store.list_ready_cohorts()
        write_snapshot(
            deps.settings.data_dir,
            active=active,
            recent=list_recent(),
            ready_cohorts=ready_cohorts,
            cold_build=cold_lifecycle_snapshot(),
            points_remaining=points_remaining,
            points_limit=points_limit,
        )
    except (OSError, TypeError, ValueError) as e:
        # Contrato non-fatal (D-34): observabilidade nunca derruba o worker. O
        # `except OSError` original era estreito demais — um TypeError de
        # serializacao escapou e matou a task no smoke de hot-path #1.
        log.warning(
            "discord_bot.ops_snapshot_write_failed", error=str(e), error_type=type(e).__name__
        )


class WorkerSupervisor:
    """RC.5/RC.6 — a pergunta certa nao e "o worker ja iniciou alguma vez?" e
    sim "existe um worker vivo agora?". O boolean historico da T1.8 deixava o
    sistema permanentemente sem consumidor depois que a task morria: o guard
    continuava True e nem uma reconexao recriava o worker.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None

    @property
    def is_alive(self) -> bool:
        return self._task is not None and not self._task.done()

    def ensure_running(self, factory: Callable[[], Coroutine[Any, Any, None]]) -> bool:
        """Cria um worker somente se nao houver um vivo. Devolve True quando
        criou. Nunca deixa dois workers concorrentes (RC.7).
        """
        if self.is_alive:
            return False
        restarted = self._task is not None
        task = asyncio.get_event_loop().create_task(factory())
        task.add_done_callback(self._on_done)
        self._task = task
        log.info("discord_bot.worker_restarted" if restarted else "discord_bot.worker_started")
        return True

    def _on_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            log.info("discord_bot.worker_stopped", reason="cancelled")
            return
        error = task.exception()
        if error is None:
            log.info("discord_bot.worker_stopped", reason="returned")
        else:
            # Nao deveria acontecer: _run_one_job ja isola cada job. Se chegar
            # aqui e bug do proprio loop, e precisa aparecer nos logs.
            log.error("discord_bot.worker_crashed", error=repr(error), exc_info=error)


def _enqueue_message(result: EnqueueResult) -> str:
    if result.job is None:
        return f"❌ {result.rejected_reason}"
    if result.deduped:
        return "⏳ Essa análise já está na fila/em andamento — você será avisado quando terminar."
    return (
        f"🔧 Coorte de referência ainda não pronta — job enfileirado "
        f"(posição {result.queue_position} na fila). Você será avisado quando terminar."
    )


def build_bot(deps: Deps) -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)
    queue = JobQueue(deps.store)
    supervisor = WorkerSupervisor()

    @bot.event
    async def on_ready() -> None:
        n_reverted = queue.recover_from_crash()
        if n_reverted:
            log.info("discord_bot.crash_recovery", n_reverted=n_reverted)
        # RC.5/RC.7: reconexao com worker vivo nao cria um segundo; worker morto
        # (crash anterior) e recriado aqui em vez de ficar sem consumidor.
        if supervisor.ensure_running(lambda: _worker_loop(bot, deps, queue)):
            # D-34: publica já no boot, para que `ops-status` responda desde o
            # primeiro segundo em vez de esperar o primeiro tick do worker.
            _publish_snapshot(deps, queue, queue.list_active(), None, None)
        log.info("discord_bot.ready", user=str(bot.user))

    @bot.command(name="analisar")
    async def cmd_analisar(ctx: commands.Context, char_name: str, report_link: str) -> None:
        """Uso: !analisar NomeDoPlayer LinkDoWCL"""
        code, fight_id = parse_report_input(report_link)
        if not code or not fight_id:
            await ctx.send(
                "❌ Fight não encontrado no link (certifique-se de incluir "
                "`?fight=X` no link do WCL)."
            )
            return

        req = AnalysisRequest(report_code=code, fight_id=fight_id, character_name=char_name)
        loop = asyncio.get_running_loop()
        # T1.7/T1.8: allow_cold_build=False — the fast path never builds a
        # 100-log cohort synchronously; a cache miss falls back to the job
        # queue (§8 "nunca baixa 100 logs de forma síncrona").
        call = functools.partial(run_analysis, req, deps, allow_cold_build=False)
        try:
            result = await loop.run_in_executor(None, call)
        except (PlayerNotFound, FightNotFound):
            await ctx.send(
                f"❌ Jogador `{char_name}` não foi encontrado neste fight "
                "ou ocorreu um erro na busca."
            )
            return
        except ScopeRejected as e:
            await ctx.send(f"❌ {e}")
            return
        except CohortNotReady:
            enqueue_result = queue.enqueue(
                job_type="analyze",
                dedup_key=f"{code}:{fight_id}:{char_name}",
                discord_user_id=str(ctx.author.id),
                discord_channel_id=str(ctx.channel.id),
            )
            await ctx.send(_enqueue_message(enqueue_result))
            return
        except InsufficientCohort as e:
            await ctx.send(
                "❌ Não há kills comparáveis suficientes para uma análise confiável "
                f"({e.n_members} logs, mínimo {e.minimum_required}). Isso costuma acontecer em "
                "encontros pouco populares ou com duração de kill atípica."
            )
            return
        except ApiError as e:
            log.warning("discord_bot.api_error", error=str(e))
            await ctx.send("❌ Erro ao consultar a API do WCL. Tente novamente em alguns minutos.")
            return

        # T3.4: cabeçalho + Top 3 em texto, HTML como anexo.
        summary_text = render_header_and_top3(result.header, result.top_actions)
        html_report = render_html_report(
            result.header,
            result.comparisons,
            manifest=result.manifest,
            build_divergence=result.build_divergence,
            performance=result.performance,
            dps_gap=result.dps_gap,
            top_actions=result.top_actions,
            duration_s=result.header.duration_max_s,
        )
        # A.3: persistir e condicao ANTERIOR a rede. O primeiro RC so cobriu o
        # caminho da fila; o smoke seguinte mostrou o interativo perdendo o
        # relatorio num 403 e ainda dizendo ao usuario que o preservara.
        artifact_id = interactive_artifact_id(code, fight_id, char_name)
        # ctx satisfaz Sendable/o extrator de contexto em runtime; as sobrecargas
        # de Context.send do discord.py nao casam nominalmente com o Protocol.
        context = context_from_discord(ctx, job_id=artifact_id)
        try:
            persist_report(deps.settings.data_dir, artifact_id, html_report)
        except ReportPersistenceError as e:
            # A.5: sem artefato, nao se tenta anexar nada e nao se afirma
            # preservacao. O processo segue saudavel.
            log.error(
                "discord_bot.interactive_report_persistence_failed",
                error=str(e),
                **context.fields(),
            )
            await send_text(
                ctx,  # type: ignore[arg-type]
                content=(
                    "⚠️ A análise foi concluída, mas não consegui guardar o relatório "
                    "neste servidor. Refaça a análise para obtê-lo."
                ),
                context=context,
            )
            return
        log.info("discord_bot.interactive_report_persisted", **context.fields())

        await send_report(
            ctx,  # type: ignore[arg-type]
            summary=summary_text,
            html=html_report,
            context=context,
            preserved=True,
        )

    @bot.command(name="status")
    async def cmd_status(ctx: commands.Context) -> None:
        """Uso: !status — fila de análises/coortes e saúde do worker."""
        # RC.14: antes o bot podia parecer saudável com o worker morto. O
        # estado do gateway (estarmos respondendo) não implica consumidor vivo.
        worker_line = (
            "⚙️ Worker: **rodando**"
            if supervisor.is_alive
            else "🛑 Worker: **parado/travado** — jobs na fila não estão sendo processados."
        )
        active = queue.list_active()
        if not active:
            await ctx.send("📋 Fila vazia - nenhum job ativo.\n" + worker_line)
            return

        lines = ["📋 **Fila de análises**"]
        for i, job in enumerate(active, start=1):
            status_icon = "🏃" if job.status == "running" else "⏳"
            lines.append(
                f"{i}. {status_icon} `{job.job_type}` — <@{job.discord_user_id}> ({job.status})"
            )
        lines.append(worker_line)
        await ctx.send("\n".join(lines))

    return bot
