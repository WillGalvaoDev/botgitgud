"""T1.6 — Discord glue, replacing bot.py's cmd_analisar. Parses the raw
command input, runs the analysis pipeline (via a thread executor — a real
persistent job queue is T1.8's job), renders the result, and sends it. No
analysis logic lives here (§1.1/T1.6): every failure path translates a
specific errors.py exception into a Portuguese user-facing message, the
one place allowed to do that broad a translation (§1.3).
"""

from __future__ import annotations

import asyncio
import re

import discord
import structlog
from discord.ext import commands

from botgitgud.analysis.pipeline import AnalysisRequest, Deps, run_analysis
from botgitgud.errors import (
    ApiError,
    FightNotFound,
    InsufficientCohort,
    PlayerNotFound,
    ScopeRejected,
)
from botgitgud.report.text import chunk_report_for_discord, render_report

log = structlog.get_logger(__name__)


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


def build_bot(deps: Deps) -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready() -> None:
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

        await ctx.send(
            f"🔍 Analisando **{char_name}** com até 100 logs de referência "
            "(Duração de kill compatível)..."
        )

        req = AnalysisRequest(report_code=code, fight_id=fight_id, character_name=char_name)
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, run_analysis, req, deps)
        except (PlayerNotFound, FightNotFound):
            await ctx.send(
                f"❌ Jogador `{char_name}` não foi encontrado neste fight "
                "ou ocorreu um erro na busca."
            )
            return
        except ScopeRejected as e:
            await ctx.send(f"❌ {e}")
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

        report_text = render_report(result.header, result.comparisons)
        chunk_max = deps.settings.discord_chunk_max_len
        for chunk in chunk_report_for_discord(report_text, max_len=chunk_max):
            await ctx.send(f"```markdown\n{chunk}\n```")

    return bot
