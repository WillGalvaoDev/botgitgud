"""T1.6/T1.7 — CLI entrypoint, replacing bot.py's Discord-only interface
for batch/debug work (docs/implementacao.md §1.1/T1.6 step 4): lets the
pipeline be exercised without Discord.

Every public command is implemented. The former `backfill` placeholder was
removed for v1.0 because no product requirement or caller exists (D-13).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from botgitgud.analysis.cohort_builder import BucketBuildResult, CohortState, build_cohorts
from botgitgud.analysis.pipeline import AnalysisRequest, Deps, run_analysis
from botgitgud.blizzard.client import BlizzardClient, BlizzardClientConfig
from botgitgud.bot.ops_snapshot import ColdBuildPublisher
from botgitgud.cli_discovery import (
    add_dataset_status_parser,
    add_discover_parser,
    add_triage_parser,
)
from botgitgud.cli_experiment import (
    add_experiment_collect_parser,
    add_experiment_plan_parser,
    add_experiment_status_parser,
)
from botgitgud.cli_experiment_calibrate import add_experiment_calibrate_parser
from botgitgud.cli_experiment_decide import add_experiment_decide_parser
from botgitgud.cli_experiment_evaluate import add_experiment_evaluate_parser
from botgitgud.cli_ops import add_ops_parsers
from botgitgud.config import Settings
from botgitgud.domain.spells import CATALOG_FILENAME, open_runtime_catalog
from botgitgud.errors import BotGitGudError, CohortDeferredBudget, RateLimitBudgetExceeded
from botgitgud.ingest.log_fetcher import LogFetcher
from botgitgud.ingest.store import Store
from botgitgud.logging_setup import configure_logging
from botgitgud.phase4.registry import Phase4ModelRegistry
from botgitgud.phase4.resolver import Phase4ModelResolver
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
    # D-35: producao le o seed versionado no maximo uma vez (primeiro boot) e
    # so escreve no cache de runtime sob data_dir, fora do Git.
    catalog = open_runtime_catalog(
        settings.data_dir, blizzard=blizzard, seed_path=Path(CATALOG_FILENAME)
    )
    store = Store(settings.data_dir)
    fetcher = LogFetcher(client, store, catalog)
    phase4_resolver = Phase4ModelResolver(Phase4ModelRegistry(store))
    return Deps(
        client=client,
        fetcher=fetcher,
        store=store,
        catalog=catalog,
        settings=settings,
        phase4_resolver=phase4_resolver,
    )


def _cmd_analyze(args: argparse.Namespace) -> int:
    settings = Settings()  # type: ignore[call-arg]  # populated from .env at runtime
    deps = _build_deps(settings)
    req = AnalysisRequest(report_code=args.report, fight_id=args.fight, character_name=args.char)
    try:
        result = run_analysis(req, deps)
    except CohortDeferredBudget as e:
        # Trabalho valido aguardando orcamento, nao erro: o progresso ficou no
        # cache e repetir o comando depois do reset continua de onde parou.
        sys.stderr.write(
            f"Coorte adiada pelo orçamento da WCL: {e}\n"
            "  resume: execute o mesmo comando após o reset de orçamento\n"
        )
        return EX_TEMPFAIL
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


def _render_bucket(result: BucketBuildResult) -> str:
    """Mensagem honesta por bucket. Num bucket adiado, `completed` é progresso —
    nunca dizer "construída" para trabalho incompleto.
    """
    span = f"[{result.duration_min_s:.0f}s-{result.duration_max_s:.0f}s]"
    head = f"bucket {result.bucket_id} {span} cohort_id={result.cohort_id}"
    counts = f"  planned: {result.planned}\n  completed: {result.completed}\n"
    if result.state is CohortState.READY:
        return f"{head}\n  Cohort ready\n{counts}"
    if result.state is CohortState.DEFERRED_BUDGET:
        return (
            f"{head}\n  Cohort prewarm deferred by WCL budget\n{counts}"
            f"  remaining: {result.planned - result.completed}\n"
            "  resume: execute the same command after budget reset\n"
        )
    return f"{head}\n  Cohort prewarm failed\n{counts}"


def build_cohort_exit_code(results: Sequence[BucketBuildResult]) -> int:
    """Agregado explícito: trabalho incompleto por orçamento nunca é sucesso.

    - qualquer FAILED             -> 1  (hard failure domina)
    - algum DEFERRED, sem failure -> 75 (EX_TEMPFAIL, retomável)
    - todos READY                 -> 0
    - nenhum bucket elegível      -> 1  (nada foi preparado)
    """
    if not results:
        return 1
    if any(r.state is CohortState.FAILED for r in results):
        return 1
    if any(r.state is CohortState.DEFERRED_BUDGET for r in results):
        return EX_TEMPFAIL
    return 0


def _cmd_build_cohort(args: argparse.Namespace) -> int:
    settings = Settings()  # type: ignore[call-arg]  # populated from .env at runtime
    deps = _build_deps(settings)
    # D-34: durante o prewarm este processo é o dono do DuckDB, então é ele quem
    # publica o ops-snapshot. Sem isso `ops-status` fica com o snapshot obsoleto
    # do bot e não enxerga progresso nenhum.
    publisher = ColdBuildPublisher(settings.data_dir, budget=deps.client)
    try:
        with publisher:
            # Contexto estatico: nunca define stage. O lifecycle real (preflight ->
            # building -> desfecho) vem do proprio build_cohorts.
            publisher.set_context(requested_bucket=args.duration_bucket)
            try:
                results = build_cohorts(
                    deps,
                    encounter_id=args.encounter,
                    class_name=args.klass,
                    spec_name=args.spec,
                    difficulty=args.difficulty,
                    duration_bucket_s=args.duration_bucket,
                )
            except CohortDeferredBudget as e:
                publisher.record("deferred_budget", outcome="deferred_budget")
                sys.stderr.write(
                    "Cohort prewarm deferred by WCL budget "
                    "(nenhuma referência coube no orçamento atual).\n"
                    f"  {e}\n"
                    "  resume: execute the same command after budget reset\n"
                )
                return EX_TEMPFAIL
            except RateLimitBudgetExceeded as e:
                publisher.record("deferred_budget", outcome="rate_limit")
                sys.stderr.write(
                    "Cohort prewarm deferred by WCL budget — progresso parcial preservado.\n"
                    f"  {e}\n"
                    "  resume: execute the same command after budget reset\n"
                )
                return EX_TEMPFAIL
            except BotGitGudError as e:
                publisher.record("failed", outcome="failed")
                sys.stderr.write(f"erro: {e}\n")
                return 1

            exit_code = build_cohort_exit_code(results)
            publisher.record(
                "completed" if exit_code == 0 else "deferred_budget",
                outcome={0: "ready", EX_TEMPFAIL: "deferred_budget"}.get(exit_code, "failed"),
                buckets=[
                    {
                        "cohort_id": r.cohort_id,
                        "bucket_id": r.bucket_id,
                        "state": str(r.state),
                        "planned": r.planned,
                        "completed": r.completed,
                        "remaining": r.planned - r.completed,
                    }
                    for r in results
                ],
            )
    finally:
        deps.store.close()
        deps.client.close()

    stream = sys.stdout if exit_code == 0 else sys.stderr
    for r in results:
        stream.write(_render_bucket(r))
    if exit_code == 0:
        stream.write(f"\n{len(results)} coorte(s) pronta(s).\n")
    else:
        stream.write("\nPrewarm incompleto: repita o mesmo comando após o reset de orçamento.\n")
    return exit_code


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
    add_dataset_status_parser(sub, build_deps=_build_deps)
    add_experiment_plan_parser(sub, build_deps=_build_deps)
    add_experiment_status_parser(sub, build_deps=_build_deps)
    add_experiment_collect_parser(sub, build_deps=_build_deps)
    add_experiment_evaluate_parser(sub)
    add_experiment_decide_parser(sub)
    add_experiment_calibrate_parser(sub)
    add_ops_parsers(sub)

    p_probe = sub.add_parser("probe-schema", help="Sonda o schema WCL v2 ao vivo (T0.1).")
    p_probe.set_defaults(func=_cmd_probe_schema)

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
