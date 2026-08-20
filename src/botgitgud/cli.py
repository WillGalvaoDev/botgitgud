"""T1.6/T1.7 — CLI entrypoint, replacing bot.py's Discord-only interface
for batch/debug work (docs/implementacao.md §1.1/T1.6 step 4): lets the
pipeline be exercised without Discord.

`analyze`, `build-cohort`, and `probe-schema` are fully implemented.
`backfill` (mentioned once in the spec with zero further detail anywhere
in the document) is a documented stub — see docs/desvios.md D-13.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from botgitgud.analysis.cohort_builder import build_cohorts
from botgitgud.analysis.pipeline import AnalysisRequest, Deps, run_analysis
from botgitgud.blizzard.client import BlizzardClient, BlizzardClientConfig
from botgitgud.cli_discovery import add_discover_parser, add_triage_parser
from botgitgud.config import Settings
from botgitgud.domain.spells import SpellCatalog
from botgitgud.errors import BotGitGudError, RateLimitBudgetExceeded
from botgitgud.ingest.log_fetcher import LogFetcher
from botgitgud.ingest.store import Store
from botgitgud.logging_setup import configure_logging
from botgitgud.report.text import render_report
from botgitgud.wcl.client import WclClient, WclClientConfig

EX_TEMPFAIL = 75  # BSD sysexits.h — T1.7's build-cohort uses this on budget exhaustion


def _build_deps(settings: Settings) -> Deps:
    client = WclClient(
        WclClientConfig(
            client_id=settings.wcl_client_id.get_secret_value(),
            client_secret=settings.wcl_client_secret.get_secret_value(),
            connect_timeout=settings.wcl_connect_timeout_s,
            read_timeout=settings.wcl_read_timeout_s,
            pool_timeout=settings.wcl_pool_timeout_s,
            max_attempts=settings.wcl_max_attempts,
            backoff_base=settings.wcl_backoff_base_s,
            backoff_factor=settings.wcl_backoff_factor,
            api_points_floor=settings.api_points_floor,
            rate_limit_cache_ttl=settings.wcl_rate_limit_cache_ttl_s,
        )
    )
    blizzard = BlizzardClient(
        BlizzardClientConfig(
            client_id=settings.blizzard_client_id.get_secret_value(),
            client_secret=settings.blizzard_client_secret.get_secret_value(),
            connect_timeout=settings.blizzard_connect_timeout_s,
            read_timeout=settings.blizzard_read_timeout_s,
            max_attempts=settings.blizzard_max_attempts,
            backoff_base=settings.blizzard_backoff_base_s,
            backoff_factor=settings.blizzard_backoff_factor,
        )
    )
    catalog = SpellCatalog(Path("spells.json"), blizzard=blizzard)
    store = Store(settings.data_dir)
    fetcher = LogFetcher(client, store, catalog)
    return Deps(client=client, fetcher=fetcher, store=store, catalog=catalog, settings=settings)


def _cmd_analyze(args: argparse.Namespace) -> int:
    settings = Settings()  # type: ignore[call-arg]  # populated from .env at runtime
    deps = _build_deps(settings)
    req = AnalysisRequest(report_code=args.report, fight_id=args.fight, character_name=args.char)
    try:
        result = run_analysis(req, deps)
    except BotGitGudError as e:
        sys.stderr.write(f"erro: {e}\n")
        return 1
    finally:
        deps.store.close()
        deps.client.close()

    report_text = render_report(
        result.header,
        result.comparisons,
        result.manifest,
        result.build_divergence,
        result.performance,
        result.dps_gap,
        result.top_actions,
    )
    sys.stdout.write(report_text + "\n")
    return 0


def _cmd_probe_schema(_args: argparse.Namespace) -> int:
    from botgitgud.wcl.schema_probe import main as probe_main

    return probe_main()


def _cmd_build_cohort(args: argparse.Namespace) -> int:
    settings = Settings()  # type: ignore[call-arg]  # populated from .env at runtime
    deps = _build_deps(settings)
    try:
        results = build_cohorts(
            deps,
            encounter_id=args.encounter,
            class_name=args.klass,
            spec_name=args.spec,
            difficulty=args.difficulty,
            duration_bucket_s=args.duration_bucket,
        )
    except RateLimitBudgetExceeded as e:
        sys.stderr.write(f"orçamento de API esgotado — progresso parcial salvo. {e}\n")
        return EX_TEMPFAIL
    except BotGitGudError as e:
        sys.stderr.write(f"erro: {e}\n")
        return 1
    finally:
        deps.store.close()
        deps.client.close()

    for r in results:
        sys.stdout.write(
            f"bucket {r.bucket_id} [{r.duration_min_s:.0f}s-{r.duration_max_s:.0f}s]: "
            f"{r.n_members} membros (cohort_id={r.cohort_id})\n"
        )
    sys.stdout.write(f"\n{len(results)} coorte(s) construída(s).\n")
    return 0


def _cmd_backfill(_args: argparse.Namespace) -> int:
    sys.stderr.write(
        "backfill ainda não implementado — sem especificação (docs/desvios.md D-13).\n"
    )
    return 1


def _cmd_serve(_args: argparse.Namespace) -> int:
    """T1.8 (docs/desvios.md D-23): no task in the plan ever wires
    bot/discord_bot.py's build_bot() into an actual entrypoint — this is
    the only place that starts the long-running Discord bot process.
    """
    settings = Settings()  # type: ignore[call-arg]  # populated from .env at runtime
    deps = _build_deps(settings)
    from botgitgud.bot.discord_bot import build_bot

    bot = build_bot(deps)
    try:
        bot.run(settings.discord_token.get_secret_value())
    finally:
        deps.store.close()
        deps.client.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="botgitgud")
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="Analisa um jogador em um fight de um report WCL.")
    p_analyze.add_argument("--report", required=True, help="Código do report WCL (16 caracteres).")
    p_analyze.add_argument("--fight", required=True, type=int, help="ID do fight dentro do report.")
    p_analyze.add_argument("--char", required=True, help="Nome do personagem.")
    p_analyze.set_defaults(func=_cmd_analyze)

    p_build_cohort = sub.add_parser(
        "build-cohort", help="Aquece o(s) pool(s) de candidatos de coorte para um encontro/spec."
    )
    p_build_cohort.add_argument("--encounter", required=True, type=int, help="encounterID da WCL.")
    # docs/desvios.md D-13: a especificação do documento não inclui --class,
    # mas className+specName são ambos obrigatórios em characterRankings —
    # specName sozinho não desambigua (ex.: "Frost" existe para Death
    # Knight e Mage). "class" é palavra reservada em Python: dest="klass".
    p_build_cohort.add_argument(
        "--class", dest="klass", required=True, help="Nome da classe (ex.: Warlock)."
    )
    p_build_cohort.add_argument("--spec", required=True, help="Nome da spec (ex.: Demonology).")
    p_build_cohort.add_argument(
        "--difficulty", required=True, type=int, help="3=Normal, 4=Heroic, 5=Mythic."
    )
    p_build_cohort.add_argument(
        "--duration-bucket",
        type=float,
        default=None,
        help="Duração em segundos; constrói só o bucket que a contém. "
        "Omitido: constrói todo bucket com candidatos suficientes.",
    )
    p_build_cohort.set_defaults(func=_cmd_build_cohort)

    add_discover_parser(sub, build_deps=_build_deps)
    add_triage_parser(sub, build_deps=_build_deps)

    p_probe = sub.add_parser("probe-schema", help="Sonda o schema WCL v2 ao vivo (T0.1).")
    p_probe.set_defaults(func=_cmd_probe_schema)

    p_backfill = sub.add_parser("backfill", help="Placeholder sem especificação (D-13).")
    p_backfill.set_defaults(func=_cmd_backfill)

    p_serve = sub.add_parser("serve", help="Inicia o bot do Discord (processo de longa duração).")
    p_serve.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
