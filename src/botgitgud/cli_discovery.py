"""T-DG.3/T-DG.4 split of cli.py's Data Acquisition Gate subcommands
(`discover`, `triage`) out of cli.py itself, to keep it under the 300-line
limit (docs/implementacao.md T1.6's rule, still enforced repo-wide) now
that both stages have their own argparse wiring. See
docs/fase4-data-acquisition-plan.md for the full design; cli.py wires
`build_deps` in via `functools.partial` to avoid a circular import (this
module needs it, cli.py already owns it for every other subcommand).
"""

from __future__ import annotations

import argparse
import functools
import sys
from collections.abc import Callable

from botgitgud.analysis.pipeline import Deps
from botgitgud.config import Settings
from botgitgud.errors import BotGitGudError
from botgitgud.ingest.discovery import run_discovery
from botgitgud.ingest.discovery_store import DiscoveryStore
from botgitgud.ingest.triage import triage_pending_reports

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
        "reports (docs/fase4-data-acquisition-plan.md).",
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
        "report descoberto e ainda não triado (docs/fase4-data-acquisition-plan.md).",
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
