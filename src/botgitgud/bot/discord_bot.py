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

import discord
import structlog
from discord.ext import commands

from botgitgud.analysis.pipeline import AnalysisRequest, Deps, run_analysis
from botgitgud.bot.job_models import BudgetStatus, EnqueueResult
from botgitgud.bot.jobs import JobQueue
from botgitgud.bot.worker import JobOutcome, run_claimed_job
from botgitgud.errors import (
    ApiError,
    CohortNotReady,
    FightNotFound,
    InsufficientCohort,
    PlayerNotFound,
    ScopeRejected,
)
from botgitgud.report.text import chunk_report_for_discord, render_report

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


async def _notify_outcome(bot: commands.Bot, outcome: JobOutcome) -> None:
    if outcome.requeued:
        # T1.8 §3: budget ran out mid-job — silently back to `queued` for
        # after pointsResetIn, no user-facing noise (it isn't done, and
        # it isn't an error the user needs to react to).
        return
    channel = bot.get_channel(int(outcome.job.discord_channel_id))
    if channel is None:
        log.warning("discord_bot.notify_channel_missing", job_id=outcome.job.job_id)
        return
    mention = f"<@{outcome.job.discord_user_id}>"
    if not outcome.ok:
        await channel.send(f"{mention} ❌ {outcome.message}")  # type: ignore[union-attr]
        return
    if outcome.job.job_type == "analyze":
        await channel.send(f"{mention} ✅ análise concluída:")  # type: ignore[union-attr]
        for chunk in chunk_report_for_discord(outcome.message):
            await channel.send(f"```markdown\n{chunk}\n```")  # type: ignore[union-attr]
    else:
        await channel.send(f"{mention} ✅ {outcome.message}")  # type: ignore[union-attr]


async def _worker_loop(bot: commands.Bot, deps: Deps, queue: JobQueue) -> None:
    while True:
        await asyncio.sleep(_WORKER_POLL_INTERVAL_S)
        if not any(j.status == "queued" for j in queue.list_active()):
            continue  # never spend a rate-limit check when there's nothing to run

        try:
            budget = _current_budget(deps)
        except ApiError as e:
            log.warning("discord_bot.budget_check_failed", error=str(e))
            continue

        job = queue.claim_next(budget)
        if job is None:
            continue

        loop = asyncio.get_running_loop()
        outcome = await loop.run_in_executor(None, run_claimed_job, queue, job, deps)
        await _notify_outcome(bot, outcome)


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
    worker_started = {"started": False}

    @bot.event
    async def on_ready() -> None:
        n_reverted = queue.recover_from_crash()
        if n_reverted:
            log.info("discord_bot.crash_recovery", n_reverted=n_reverted)
        if not worker_started["started"]:
            bot.loop.create_task(_worker_loop(bot, deps, queue))
            worker_started["started"] = True
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

        report_text = render_report(
            result.header, result.comparisons, result.manifest, result.build_divergence
        )
        chunk_max = deps.settings.discord_chunk_max_len
        for chunk in chunk_report_for_discord(report_text, max_len=chunk_max):
            await ctx.send(f"```markdown\n{chunk}\n```")

    @bot.command(name="status")
    async def cmd_status(ctx: commands.Context) -> None:
        """Uso: !status — mostra a fila de análises/coortes em andamento."""
        active = queue.list_active()
        if not active:
            await ctx.send("📋 Fila vazia — nenhum job ativo.")
            return

        lines = ["📋 **Fila de análises**"]
        for i, job in enumerate(active, start=1):
            status_icon = "🏃" if job.status == "running" else "⏳"
            lines.append(
                f"{i}. {status_icon} `{job.job_type}` — <@{job.discord_user_id}> ({job.status})"
            )
        await ctx.send("\n".join(lines))

    return bot
