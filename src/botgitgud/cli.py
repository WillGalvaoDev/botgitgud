"""T1.6/T1.7 — CLI entrypoint, replacing bot.py's Discord-only interface
for batch/debug work (docs/implementacao.md §1.1/T1.6 step 4): lets the
pipeline be exercised without Discord.

Every public command is implemented. The former `backfill` placeholder was
removed for v1.0 because no product requirement or caller exists (D-13).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import duckdb
import structlog

from botgitgud.analysis.cohort_builder import BucketBuildResult, CohortState, build_cohorts
from botgitgud.analysis.cohort_invalidation import (
    CohortPoolAssessment,
    InvalidationPlan,
    plan_invalidation,
)
from botgitgud.analysis.pipeline import AnalysisRequest, Deps, run_analysis
from botgitgud.blizzard.client import BlizzardClient, BlizzardClientConfig
from botgitgud.bot.ops_snapshot import ColdBuildPublisher
from botgitgud.cli_deploy import add_deploy_parsers
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
from botgitgud.logging_setup import (
    LOG_FILENAME,
    configure_logging,
    enable_file_logging,
    log_dir_for,
)
from botgitgud.phase4.registry import Phase4ModelRegistry
from botgitgud.phase4.resolver import Phase4ModelResolver
from botgitgud.report.render import render_text_report
from botgitgud.wcl.client import WclClient, WclClientConfig

log = structlog.get_logger(__name__)

EX_TEMPFAIL = 75  # BSD sysexits.h — T1.7's build-cohort uses this on budget exhaustion


def _read_cohort_registry_read_only(database: Path) -> list[dict[str, object]]:
    """Read registry rows without Store initialization or DDL."""
    connection = duckdb.connect(str(database), read_only=True)
    try:
        cursor = connection.execute(
            """SELECT cohort_id, difficulty, partition, n_members
               FROM cohort_registry ORDER BY difficulty, partition, cohort_id"""
        )
        keys = ("cohort_id", "difficulty", "partition", "n_members")
        return [dict(zip(keys, row, strict=True)) for row in cursor.fetchall()]
    finally:
        connection.close()


def _plan_as_dict(
    plan: InvalidationPlan, *, backup_present: bool | None = None
) -> dict[str, object]:
    def item(assessment: CohortPoolAssessment) -> dict[str, object]:
        return {
            "cohort_id": assessment.cohort_id,
            "difficulty": assessment.difficulty,
            "n_members": assessment.n_members,
            "partition": assessment.partition,
            "reasons": list(assessment.reasons),
            "status": assessment.status,
        }

    return {
        "affected_total": plan.affected_total,
        "backup_present": backup_present,
        "counts_by_difficulty_partition": [
            {"count": count, "difficulty": difficulty, "partition": partition}
            for difficulty, partition, count in plan.counts_by_difficulty_partition
        ],
        "preserved": [item(a) for a in plan.preserved],
        "requires_refetch": bool(plan.to_invalidate),
        # Lower-bound estimate: one rankings query and one logical log fetch
        # per recorded member.  No query is executed by this report.
        "estimated_refetch_queries": sum(a.n_members + 1 for a in plan.to_invalidate),
        "to_invalidate": [item(a) for a in plan.to_invalidate],
        "total": plan.total,
        "assessed_total": plan.assessed_total,
    }


def _cmd_plan_cohort_invalidation(args: argparse.Namespace) -> int:
    database = args.database or args.data_dir / "warehouse.duckdb"
    try:
        rows = _read_cohort_registry_read_only(database)
    except Exception as exc:
        sys.stderr.write(
            f"erro: não foi possível abrir o store em modo somente leitura: {database} ({exc})\n"
        )
        return 1

    plan = plan_invalidation(rows)
    backup_present = any(database.parent.glob(f"{database.name}*.bak"))
    rendered = _plan_as_dict(plan, backup_present=backup_present)
    if args.json:
        sys.stdout.write(json.dumps(rendered, ensure_ascii=False, sort_keys=True) + "\n")
        return 0

    sys.stdout.write(
        f"assessed={plan.assessed_total} affected={plan.total} preserved={len(plan.preserved)}\n"
    )
    sys.stdout.write(
        f"backup_present={str(backup_present).lower()} "
        f"requires_refetch={str(bool(plan.to_invalidate)).lower()} "
        f"estimated_refetch_queries={rendered['estimated_refetch_queries']}\n"
    )
    for difficulty, partition, count in plan.counts_by_difficulty_partition:
        sys.stdout.write(f"difficulty={difficulty} partition={partition} affected={count}\n")
    for assessment in plan.to_invalidate:
        sys.stdout.write(
            f"INVALIDATE {assessment.cohort_id} difficulty={assessment.difficulty} "
            f"partition={assessment.partition} reasons={','.join(assessment.reasons)}\n"
        )
    for assessment in plan.preserved:
        sys.stdout.write(f"PRESERVE {assessment.cohort_id} status={assessment.status}\n")
    return 0


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

    # RP.3: o CLI também passa pelo `ReportContract` — mesmo guarda de
    # Top 3 execution-only que os caminhos do Discord.
    report_text = render_text_report(result)
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
    _start_operational_log(settings, command="build-cohort")
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


def _start_operational_log(settings: Settings, *, command: str) -> str:
    """Liga a trilha durável e registra o início do processo.

    Só os comandos de longa duração a ligam: um `analyze` ou um `ops-status`
    de dez segundos não é um soak, e criar `data/logs/` a cada invocação de
    CLI só encheria o disco de ruído.
    """
    session_id = enable_file_logging(
        settings.data_dir,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
    )
    log.info(
        "process.started",
        command=command,
        data_dir=str(settings.data_dir),
        log_file=str(log_dir_for(settings.data_dir) / LOG_FILENAME),
    )
    return session_id


def _cmd_serve(_args: argparse.Namespace) -> int:
    """T1.8 (docs/desvios.md D-23): no task in the plan ever wires
    bot/discord_bot.py's build_bot() into an actual entrypoint — this is
    the only place that starts the long-running Discord bot process.
    """
    settings = Settings()  # type: ignore[call-arg]  # populated from .env at runtime
    session_id = _start_operational_log(settings, command="serve")
    # O operador precisa do id ANTES de o soak comecar, para saber o que filtrar
    # depois; stdout pode ter sumido quando a auditoria acontecer.
    sys.stdout.write(f"session_id={session_id}\n")
    deps = _build_deps(settings)
    from botgitgud.bot.discord_bot import build_bot

    try:
        # Construção permanece dentro do try/finally para que recursos locais
        # sejam liberados mesmo se o runtime falhar antes de conectar.
        bot = build_bot(deps)
        bot.run(settings.discord_token.get_secret_value())
    except BaseException as e:
        # Ultimo lugar em que uma queda do processo ainda pode deixar
        # evidencia. Sem isto, um crash de 24h de soak vira um arquivo que
        # simplesmente para de crescer, sem dizer por que.
        log.error("process.unexpected_error", command="serve", exc_info=e)
        raise
    finally:
        log.info("process.stopping", command="serve")
        deps.store.close()
        deps.client.close()
        log.info("process.stopped", command="serve")
    return 0


def _cmd_supervise(_args: argparse.Namespace) -> int:
    """B6 — supervisão EXTERNA ao processo `serve`. Nunca chama `_build_deps`:
    o supervisor não fala com WCL nem Discord, apenas relança o filho.
    """
    from botgitgud.ops.supervisor import SupervisorSettings
    from botgitgud.ops.supervisor import main as run_supervisor

    settings = Settings()  # type: ignore[call-arg]  # populated from .env at runtime
    supervisor_settings = SupervisorSettings(
        poll_interval_s=settings.supervisor_poll_interval_s,
        backoff_base_s=settings.supervisor_backoff_base_s,
        backoff_max_s=settings.supervisor_backoff_max_s,
        backoff_reset_after_s=settings.supervisor_backoff_reset_after_s,
        storm_threshold=settings.supervisor_storm_threshold,
        storm_window_s=settings.supervisor_storm_window_s,
        stop_grace_s=settings.supervisor_stop_grace_s,
    )
    return run_supervisor(
        settings.data_dir,
        supervisor_settings,
        log_max_bytes=settings.log_max_bytes,
        log_backup_count=settings.log_backup_count,
    )


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
    add_deploy_parsers(sub)

    p_invalidation = sub.add_parser(
        "plan-cohort-invalidation",
        aliases=["cohort-invalidation-plan"],
        help="Produz, sem executar, o plano seletivo de invalidação de coortes.",
    )
    p_invalidation.add_argument(
        "--data-dir", type=Path, default=Path("data"), help="Diretório que contém warehouse.duckdb."
    )
    p_invalidation.add_argument(
        "--database", type=Path, default=None, help="Path explícito do warehouse.duckdb."
    )
    p_invalidation.add_argument("--json", action="store_true", help="Emite JSON determinístico.")
    p_invalidation.set_defaults(func=_cmd_plan_cohort_invalidation)

    p_probe = sub.add_parser("probe-schema", help="Sonda o schema WCL v2 ao vivo (T0.1).")
    p_probe.set_defaults(func=_cmd_probe_schema)

    p_serve = sub.add_parser("serve", help="Inicia o bot do Discord (processo de longa duração).")
    p_serve.set_defaults(func=_cmd_serve)

    p_supervise = sub.add_parser(
        "supervise",
        help=(
            "Supervisiona `serve` como processo filho: reinicia em crash "
            "com backoff, nunca duplica, para em restart storm (B6)."
        ),
    )
    p_supervise.set_defaults(func=_cmd_supervise)

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
