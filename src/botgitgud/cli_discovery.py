"""T-DG.3/T-DG.4 split of cli.py's Data Acquisition Gate subcommands
(`discover`, `triage`) out of cli.py itself, to keep it under the 300-line
limit (T1.6's rule, still enforced repo-wide) now
that both stages have their own argparse wiring. See
docs/phase4.md for the full design; cli.py wires
`build_deps` in via `functools.partial` to avoid a circular import (this
module needs it, cli.py already owns it for every other subcommand).
"""

from __future__ import annotations

import argparse
import functools
import sys
from collections.abc import Callable

from botgitgud.analysis.dataset_status import (
    GATE_TARGET,
    TEMPORAL_MIN_PER_SIDE,
    CandidateGroup,
    TargetStatus,
    target_status,
    top_candidate_groups,
)
from botgitgud.analysis.pipeline import Deps
from botgitgud.config import Settings
from botgitgud.errors import BotGitGudError
from botgitgud.ingest.discovery import run_discovery
from botgitgud.ingest.discovery_store import DiscoveryStore
from botgitgud.ingest.triage import triage_pending_reports
from botgitgud.phase4.registry import Phase4ModelRegistry

EX_TEMPFAIL = 75  # BSD sysexits.h — same contract as cli.py's build-cohort/discover/triage

BuildDeps = Callable[[Settings], Deps]


def add_discover_parser(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
    *,
    build_deps: BuildDeps,
) -> None:
    p = sub.add_parser(
        "discover",
        help="Estágio A do Data Acquisition Gate: descoberta janelada e resumível de "
        "reports (docs/phase4.md).",
    )
    p.add_argument("--zone", required=True, type=int, help="zoneID da WCL.")
    p.add_argument("--start-ms", required=True, type=int, help="Início da janela total (epoch ms).")
    p.add_argument("--end-ms", required=True, type=int, help="Fim da janela total (epoch ms).")
    p.add_argument(
        "--window-hours",
        type=float,
        default=12.0,
        help="Tamanho de cada janela interna, em horas (default 12h; subdividida "
        "automaticamente ao esgotar a página 25 do servidor).",
    )
    p.add_argument(
        "--max-points",
        type=float,
        default=None,
        help="Teto de pontos de API para esta execução. Omitido: sem teto explícito "
        "(ainda respeita o piso global de orçamento).",
    )
    p.set_defaults(func=functools.partial(cmd_discover, build_deps=build_deps))


def add_triage_parser(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
    *,
    build_deps: BuildDeps,
) -> None:
    p = sub.add_parser(
        "triage",
        help="Estágio B do Data Acquisition Gate: triagem via report.rankings de todo "
        "report descoberto e ainda não triado (docs/phase4.md).",
    )
    p.add_argument(
        "--zone", type=int, default=None, help="Restringe aos reports desta zoneID (opcional)."
    )
    p.add_argument(
        "--max-points",
        type=float,
        default=None,
        help="Teto de pontos de API (estimados) para esta execução. Omitido: sem teto "
        "explícito (ainda respeita o piso global de orçamento).",
    )
    p.set_defaults(func=functools.partial(cmd_triage, build_deps=build_deps))


def cmd_discover(args: argparse.Namespace, *, build_deps: BuildDeps) -> int:
    """T-DG.3 — nunca dispara sozinho: cada execução processa o que couber
    dentro de --max-points (se dado) e do piso global de orçamento, e para.
    """
    settings = Settings()  # type: ignore[call-arg]  # populada a partir do .env em runtime
    deps = build_deps(settings)
    discovery_store = DiscoveryStore(deps.store)
    window_span_ms = int(args.window_hours * 3600 * 1000)
    try:
        summary = run_discovery(
            deps.client,
            discovery_store,
            zone_id=args.zone,
            start_ms=args.start_ms,
            end_ms=args.end_ms,
            window_span_ms=window_span_ms,
            max_points=args.max_points,
        )
    except BotGitGudError as e:
        sys.stderr.write(f"erro: {e}\n")
        return 1
    finally:
        deps.store.close()
        deps.client.close()

    sys.stdout.write(
        f"job_key={summary.job_key}\n"
        f"janelas: {summary.windows_done}/{summary.windows_total} concluídas "
        f"({summary.windows_remaining} restantes)\n"
        f"reports novos descobertos: {summary.reports_written}\n"
        f"pontos de API gastos: {summary.points_spent:.2f}\n"
        f"motivo de parada: {summary.stopped_reason}\n"
    )
    return EX_TEMPFAIL if summary.stopped_reason == "budget_exceeded" else 0


def cmd_triage(args: argparse.Namespace, *, build_deps: BuildDeps) -> int:
    """T-DG.4 — spec/encontro-agnóstica por construção, conforme aprovado
    pelo usuário: nunca fixa um alvo antes do censo.
    """
    settings = Settings()  # type: ignore[call-arg]  # populada a partir do .env em runtime
    deps = build_deps(settings)
    discovery_store = DiscoveryStore(deps.store)
    try:
        summary = triage_pending_reports(
            deps.client, discovery_store, zone_id=args.zone, max_points=args.max_points
        )
    except BotGitGudError as e:
        sys.stderr.write(f"erro: {e}\n")
        return 1
    finally:
        deps.store.close()
        deps.client.close()

    sys.stdout.write(
        f"reports: {summary.reports_triaged}/{summary.reports_total} triados "
        f"({summary.reports_remaining} restantes)\n"
        f"fights novos escritos: {summary.fights_written}\n"
        f"pontos de API gastos (estimativa): {summary.points_spent:.2f}\n"
        f"motivo de parada: {summary.stopped_reason}\n"
    )
    return EX_TEMPFAIL if summary.stopped_reason == "budget_exceeded" else 0


def add_dataset_status_parser(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
    *,
    build_deps: BuildDeps,
) -> None:
    p = sub.add_parser(
        "dataset-status",
        help="T-DG.5: inspeciona objetivamente o progresso do Data Acquisition Gate. "
        "Sem --class/--spec/etc.: visão geral dos melhores candidatos. Com todos: "
        "avaliação completa de um alvo, incluindo o veredito FASE 4 DATA GATE. "
        "Nunca chama a API da WCL.",
    )
    p.add_argument("--class", dest="klass", default=None, help="Nome da classe (ex.: DeathKnight).")
    p.add_argument("--spec", default=None, help="Nome da spec (ex.: Unholy).")
    p.add_argument("--encounter", type=int, default=None, help="encounterID da WCL.")
    p.add_argument("--difficulty", type=int, default=None, help="3=Normal, 4=Heroic, 5=Mythic.")
    p.add_argument("--partition", type=int, default=None, help="Partition alvo.")
    p.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Nº de grupos candidatos mostrados na visão geral (default 20).",
    )
    p.set_defaults(func=functools.partial(cmd_dataset_status, build_deps=build_deps))


def cmd_dataset_status(args: argparse.Namespace, *, build_deps: BuildDeps) -> int:
    settings = Settings()  # type: ignore[call-arg]  # populada a partir do .env em runtime
    deps = build_deps(settings)
    discovery_store = DiscoveryStore(deps.store)
    target_fields = (args.klass, args.spec, args.encounter, args.difficulty, args.partition)
    try:
        if all(f is not None for f in target_fields):
            status = target_status(
                deps.store,
                discovery_store,
                class_name=args.klass,
                spec_name=args.spec,
                encounter_id=args.encounter,
                difficulty=args.difficulty,
                partition=args.partition,
            )
            _print_target_status(status)
        elif any(f is not None for f in target_fields):
            sys.stderr.write(
                "erro: --class/--spec/--encounter/--difficulty/--partition devem ser usados "
                "juntos (ou nenhum, para a visão geral).\n"
            )
            return 1
        else:
            registry = Phase4ModelRegistry(deps.store)
            _print_candidate_groups(
                top_candidate_groups(deps.store, limit=args.limit, registry=registry)
            )
    finally:
        deps.store.close()
        deps.client.close()
    return 0


def _print_candidate_groups(groups: list[CandidateGroup]) -> None:
    if not groups:
        sys.stdout.write("nenhum candidato descoberto ainda — rode 'discover' e 'triage'.\n")
        return
    sys.stdout.write("melhores candidatos (class/spec @ encontro/dificuldade/partition):\n\n")
    for g in groups:
        sys.stdout.write(
            f"  {g.class_name}/{g.spec_name}  encounter={g.encounter_id} "
            f"difficulty={g.difficulty} partition={g.partition}: {g.n_candidates} candidatos; "
            f"ingeridos={g.ingested}; faltam={g.observations_remaining}; "
            f"gate={'PASS' if g.gate_pass else 'BLOCKED'}; modelo={g.model_status.value}\n"
        )


def _print_target_status(status: TargetStatus) -> None:
    sys.stdout.write(
        f"DATA ACQUISITION GATE — {status.class_name}/{status.spec_name} @ encounter "
        f"{status.encounter_id}, difficulty {status.difficulty}, partition {status.partition}\n\n"
        "  Descoberta\n"
        f"    reports descobertos (global) ........ {status.reports_discovered}\n"
        f"    fights triados (este alvo) .......... {status.fights_triaged}\n"
        f"    candidatos (kills desta spec) ........ {status.candidates}\n\n"
        "  Ingestão\n"
        f"    observações ingeridas ............... {status.ingested}\n"
        f"    duplicatas (ignoradas na contagem) .. {status.duplicates}\n"
        f"    rejeitadas ........................... {status.rejected}\n"
    )
    for reason, n in sorted(status.rejected_by_reason.items(), key=lambda kv: -kv[1]):
        sys.stdout.write(f"      - {reason} .... {n}\n")
    sys.stdout.write(
        f"\n  Observações VÁLIDAS: {status.valid} / {GATE_TARGET}   "
        f"({status.progress_pct:.1f}%)\n\n"
        "  Dispersão temporal\n"
        f"    mais antiga .......................... {status.earliest_valid_ingested_at}\n"
        f"    mais recente ......................... {status.latest_valid_ingested_at}\n"
        f"    corte com >= {TEMPORAL_MIN_PER_SIDE} de cada lado ......... "
        f"{'sim' if status.temporal_split_ok else 'não'}\n\n"
        "  Restante\n"
        f"    observações faltantes ............... {status.observations_remaining}\n\n"
    )
    verdict = "PASS" if status.gate_pass else "BLOCKED"
    sys.stdout.write(f"FASE 4 DATA GATE: {verdict}\n")
