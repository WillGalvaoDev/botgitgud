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
import contextvars
import functools
import re
from collections.abc import Callable, Coroutine, Sequence
from pathlib import Path
from typing import Any

import discord
import structlog
from discord.ext import commands

from botgitgud.analysis.cold_build import cold_lifecycle_snapshot
from botgitgud.analysis.pipeline import AnalysisRequest, Deps, run_analysis
from botgitgud.bot.analysis_runs import now_iso, record_analysis_result, track_analysis
from botgitgud.bot.delivery import (
    ChannelResolver,
    DeliveryContext,
    context_from_discord,
    send_report,
    send_text,
)
from botgitgud.bot.job_models import BudgetStatus, EnqueueResult, Job, JobOutcome
from botgitgud.bot.jobs import JobQueue
from botgitgud.bot.ops_snapshot import write_snapshot
from botgitgud.bot.report_store import (
    ReportPersistenceError,
    interactive_artifact_id,
    persist_report,
)
from botgitgud.bot.worker import run_claimed_job
from botgitgud.errors import (
    ApiError,
    CohortNotReady,
    FightNotFound,
    InsufficientCohort,
    PlayerNotFound,
    ScopeRejected,
)
from botgitgud.ops.control import stop_request_path_for
from botgitgud.report.html_report import render_html_report
from botgitgud.report.text import render_header_and_top3

log = structlog.get_logger(__name__)


async def run_in_executor_with_context(
    loop: asyncio.AbstractEventLoop, call: Callable[[], Any]
) -> Any:
    """Executa trabalho bloqueante preservando o recorder da analise."""
    analysis_context = contextvars.copy_context()
    return await loop.run_in_executor(None, analysis_context.run, call)


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

    EB.5: `benchmark_build` é job interno — nunca toca o Discord, nem para
    avisar sucesso/falha. `discord_channel_id` desse job_type é o sentinela
    `SYSTEM_ACTOR_ID` (bot/benchmark_job.py), não numérico de propósito —
    passá-lo para `int()` abaixo quebraria; este retorno antecipado garante
    que isso nunca acontece.
    """
    if outcome.job.job_type == "benchmark_build":
        log.info(
            "discord_bot.benchmark_job_internal_no_notice",
            job_id=outcome.job.job_id,
            ok=outcome.ok,
            deferred=outcome.deferred,
        )
        return
    if outcome.requeued or outcome.deferred:
        # T1.8 §3 / B2: o orcamento acabou no meio do job. Nos dois casos o job
        # continua elegivel e retoma sozinho, entao nao ha nada que o usuario
        # precise fazer — e repetir "ainda esperando" a cada janela viraria
        # spam. Ele ja foi avisado no enfileiramento de que a analise continua
        # sozinha, e recebera o relatorio quando ela terminar.
        if outcome.deferred:
            log.info(
                "discord_bot.job_deferred_no_notice",
                job_id=outcome.job.job_id,
                deferred_until=outcome.deferred_until,
            )
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
        # Elegibilidade == Job.is_claimable(), a MESMA regra que claim_next()
        # usa em SQL (jobs.py) — nunca "status == queued" sozinho. Incidente
        # real do soak de 2026-08-27: um `deferred_budget` com `deferred_until`
        # ja vencido nunca abria este guard porque ele so olhava "queued", e
        # claim_next() — que ja sabia reivindicar o job vencido — nunca chegava
        # a ser chamado. O job ficava preso ate, por coincidencia, outro job
        # `queued` aparecer. Reusa `active` (ja buscado para o snapshot acima):
        # nenhuma consulta nova ao banco, nenhuma chamada a WCL.
        if not any(j.is_claimable() for j in active):
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

        # `defer_count > 0` distingue retomada de primeira execucao. E o unico
        # ponto do sistema que sabe disso: depois daqui o job e so `running`.
        log.info(
            "job.resumed" if job.defer_count else "job.started",
            job_id=job.job_id,
            job_type=job.job_type,
            defer_count=job.defer_count,
            points_remaining=budget.points_remaining,
        )
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
        f"🔧 Sua análise precisa preparar uma nova coorte de referência. "
        f"Ela foi colocada na fila (posição {result.queue_position}) e continuará "
        "automaticamente quando houver orçamento na Warcraft Logs — pode levar "
        "algumas horas. Você receberá o relatório aqui quando terminar; "
        "**não é preciso repetir o comando**."
    )


async def _stop_request_watcher(bot: commands.Bot, data_dir: Path, poll_interval_s: float) -> None:
    """B6 — a metade do bot do canal de controle de `ops/control.py`.

    O supervisor externo nao manda nenhum sinal de SO: ele escreve
    `control/stop.request` e espera o processo sumir sozinho. Este watcher e
    quem torna isso um shutdown de verdade — `bot.close()` roda de dentro do
    proprio loop de eventos, onde o websocket e (via o `finally` de
    `_cmd_serve`) o DuckDB sao efetivamente liberados. Nunca remove o
    arquivo: por contrato (`ops/control.py`), so `start-bot-service.ps1` faz
    isso.
    """
    stop_path = stop_request_path_for(data_dir)
    while True:
        await asyncio.sleep(poll_interval_s)
        if stop_path.exists():
            log.info("discord_bot.stop_requested")
            await bot.close()
            return


def build_bot(deps: Deps) -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)
    queue = JobQueue(deps.store)
    supervisor = WorkerSupervisor()
    stop_watcher_started = False

    @bot.event
    async def on_connect() -> None:
        # Anterior ao `on_ready`: separa "o socket subiu" de "o bot esta
        # utilizavel". Numa reconexao noturna de soak os dois nao coincidem.
        log.info("discord_bot.gateway_connected")

    @bot.event
    async def on_ready() -> None:
        nonlocal stop_watcher_started
        n_reverted = queue.recover_from_crash()
        if n_reverted:
            log.info("discord_bot.crash_recovery", n_reverted=n_reverted)
        # RC.5/RC.7: reconexao com worker vivo nao cria um segundo; worker morto
        # (crash anterior) e recriado aqui em vez de ficar sem consumidor.
        if supervisor.ensure_running(lambda: _worker_loop(bot, deps, queue)):
            # D-34: publica já no boot, para que `ops-status` responda desde o
            # primeiro segundo em vez de esperar o primeiro tick do worker.
            _publish_snapshot(deps, queue, queue.list_active(), None, None)
        if not stop_watcher_started:
            # on_ready pode disparar de novo numa reconexao; o watcher so
            # precisa existir uma vez por processo, nao uma vez por conexao.
            asyncio.get_event_loop().create_task(
                _stop_request_watcher(
                    bot, deps.settings.data_dir, deps.settings.bot_stop_poll_interval_s
                )
            )
            stop_watcher_started = True
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
        # Telemetria auditavel: o smoke do Fiskowl funcionou mas nao pode ser
        # provado depois porque tudo vivia em stdout. O tracker publica o
        # artefato mesmo em return antecipado ou excecao.
        with track_analysis(
            deps.settings.data_dir,
            budget=deps.client,
            player=char_name,
            report_code=code,
            fight_id=fight_id,
        ) as run:
            # T1.7/T1.8: allow_cold_build=False — the fast path never builds a
            # 100-log cohort synchronously; a cache miss falls back to the job
            # queue (§8 "nunca baixa 100 logs de forma síncrona").
            call = functools.partial(run_analysis, req, deps, allow_cold_build=False)
            # asyncio.run_in_executor nao copia ContextVars. Sem esta ponte o
            # recorder ativo desaparece justamente na thread que resolve cache
            # e executa WCL, tornando o hot path impossivel de auditar.
            try:
                result = await run_in_executor_with_context(loop, call)
            except (PlayerNotFound, FightNotFound):
                run.final_status = "target_not_found"
                await ctx.send(
                    f"❌ Jogador `{char_name}` não foi encontrado neste fight "
                    "ou ocorreu um erro na busca."
                )
                return
            except ScopeRejected as e:
                run.final_status = "scope_rejected"
                await ctx.send(f"❌ {e}")
                return
            except CohortNotReady:
                run.final_status = "enqueued_cold_build"
                run.hot_path = False
                enqueue_result = queue.enqueue(
                    job_type="analyze",
                    dedup_key=f"{code}:{fight_id}:{char_name}",
                    discord_user_id=str(ctx.author.id),
                    discord_channel_id=str(ctx.channel.id),
                )
                log.info(
                    "job.queued",
                    job_id=None if enqueue_result.job is None else enqueue_result.job.job_id,
                    job_type="analyze",
                    deduped=enqueue_result.deduped,
                    rejected=enqueue_result.job is None,
                    queue_position=enqueue_result.queue_position,
                    report_code=code,
                    fight_id=fight_id,
                    player=char_name,
                    channel_id=str(ctx.channel.id),
                )
                await ctx.send(_enqueue_message(enqueue_result))
                return
            except InsufficientCohort as e:
                run.final_status = "insufficient_cohort"
                await ctx.send(
                    "❌ Não há kills comparáveis suficientes para uma análise confiável "
                    f"({e.n_members} logs, mínimo {e.minimum_required}). Isso costuma acontecer em "
                    "encontros pouco populares ou com duração de kill atípica."
                )
                return
            except ApiError as e:
                run.final_status = "wcl_api_error"
                run.error_type = type(e).__name__
                log.warning("discord_bot.api_error", error=str(e))
                await ctx.send(
                    "❌ Erro ao consultar a API do WCL. Tente novamente em alguns minutos."
                )
                return

            # Hot path confirmado: chegamos aqui sem CohortNotReady.
            run.hot_path = True
            record_analysis_result(run, result)

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
                run.final_status = "report_persistence_failed"
                run.error_type = type(e).__name__
                run.error_message = str(e)
                run.report_path_exists = False
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
            run.report_artifact_id = artifact_id
            run.report_path_exists = True
            run.report_persisted_at = now_iso()
            run.channel_id = context.channel_id
            run.guild_id = context.guild_id

            run.delivery_started_at = now_iso()
            outcome = await send_report(
                ctx,  # type: ignore[arg-type]
                summary=summary_text,
                html=html_report,
                context=context,
                preserved=True,
            )
            run.delivery_finished_at = now_iso()
            run.delivery_status = str(outcome.status)

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
            # Um job adiado por orcamento nao esta parado nem quebrado: mostrar
            # o mesmo icone de "na fila" esconderia por que ele nao anda.
            status_icon = {"running": "🏃", "deferred_budget": "💤"}.get(job.status, "⏳")
            suffix = ""
            if job.is_deferred:
                suffix = " — aguardando orçamento WCL, retoma sozinho"
            lines.append(
                f"{i}. {status_icon} `{job.job_type}` — <@{job.discord_user_id}> "
                f"({job.status}){suffix}"
            )
        lines.append(worker_line)
        await ctx.send("\n".join(lines))

    return bot
