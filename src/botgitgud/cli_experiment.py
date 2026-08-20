"""SAE.6 — `experiment-plan` and `experiment-status`
(docs/fase4-statistical-architecture-experiment.md §14 of the task brief).

Both are strictly read-only over the local warehouse and never touch the
WCL API, mirroring `dataset-status`. `experiment-plan` sizes and prices a
campaign that has not been executed; `experiment-status` reports what has
actually been collected and whether the generalization splits are yet
buildable — it is expected to run, and report zeros, before any Stage C.

Split from cli.py to keep it under the 300-line limit (T1.6), same pattern
and `build_deps` injection as cli_discovery.py.
"""

from __future__ import annotations

import argparse
import functools
import sys
from collections.abc import Callable
from datetime import UTC, datetime

from botgitgud.analysis.pipeline import Deps
from botgitgud.config import Settings
from botgitgud.domain.specs import SpecId
from botgitgud.phase4.experiment import (
    MIN_ENCOUNTERS_FOR_EXPERIMENT,
    MIN_SPECS_FOR_EXPERIMENT,
    PERCENTILE_BUCKETS,
    ExperimentBudget,
)
from botgitgud.phase4.experiment_campaign import ExperimentCampaign, StatisticalExperimentPlan
from botgitgud.phase4.experiment_planner import ExperimentPlanner
from botgitgud.phase4.experimental_dataset import (
    ExperimentalDatasetBuilder,
    ExperimentalFeatureDataset,
)

BuildDeps = Callable[[Settings], Deps]

DEFAULT_MAX_OBSERVATIONS = 1200


def _parse_spec(value: str) -> SpecId:
    if "/" not in value:
        raise argparse.ArgumentTypeError(f"spec must look like Class/Spec, got {value!r}")
    class_name, spec_name = value.split("/", 1)
    return SpecId(class_name.strip(), spec_name.strip())


def add_experiment_plan_parser(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
    *,
    build_deps: BuildDeps,
) -> None:
    p = sub.add_parser(
        "experiment-plan",
        help="SAE: dimensiona e precifica a amostra experimental do Stage C a partir "
        "dos dados de discovery/triage. Read-only, nunca chama a API.",
    )
    p.add_argument("--partition", required=True, type=int, help="Partition alvo (uma so).")
    p.add_argument(
        "--difficulty",
        required=True,
        type=int,
        nargs="+",
        help="Difficulties incluidas (ex.: --difficulty 5).",
    )
    p.add_argument(
        "--max-observations",
        type=int,
        default=DEFAULT_MAX_OBSERVATIONS,
        help=f"Teto de observacoes (default {DEFAULT_MAX_OBSERVATIONS}).",
    )
    p.add_argument(
        "--max-points",
        type=float,
        default=ExperimentBudget().max_api_points,
        help="Teto de pontos de API para TODO o Stage C experimental.",
    )
    p.add_argument(
        "--spec", action="append", type=_parse_spec, default=None, help="Class/Spec; repetivel."
    )
    p.add_argument(
        "--encounter", action="append", type=int, default=None, help="encounterID; repetivel."
    )
    p.set_defaults(func=functools.partial(cmd_experiment_plan, build_deps=build_deps))


def add_experiment_status_parser(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
    *,
    build_deps: BuildDeps,
) -> None:
    p = sub.add_parser(
        "experiment-status",
        help="SAE: relata a amostra experimental ja coletada e se os splits de "
        "generalizacao sao construiveis. Read-only, funciona com zero dados.",
    )
    p.add_argument("--partition", type=int, default=None, help="Restringe a esta partition.")
    p.add_argument("--difficulty", type=int, nargs="+", default=None, help="Restringe a estas.")
    p.set_defaults(func=functools.partial(cmd_experiment_status, build_deps=build_deps))


def cmd_experiment_plan(args: argparse.Namespace, *, build_deps: BuildDeps) -> int:
    settings = Settings()  # type: ignore[call-arg]  # populada a partir do .env em runtime
    deps = build_deps(settings)
    budget = ExperimentBudget(max_api_points=args.max_points)
    request = StatisticalExperimentPlan(
        partition=args.partition,
        difficulties=frozenset(args.difficulty),
        budget=budget,
        max_observations=args.max_observations,
        specs=frozenset(args.spec) if args.spec else None,
        encounters=frozenset(args.encounter) if args.encounter else None,
    )
    try:
        campaign = ExperimentPlanner(deps.store).plan(request)
    finally:
        deps.store.close()
        deps.client.close()
    _print_campaign(campaign)
    return 0


def cmd_experiment_status(args: argparse.Namespace, *, build_deps: BuildDeps) -> int:
    settings = Settings()  # type: ignore[call-arg]  # populada a partir do .env em runtime
    deps = build_deps(settings)
    try:
        dataset = ExperimentalDatasetBuilder(deps.store).build(
            partition=args.partition,
            difficulties=frozenset(args.difficulty) if args.difficulty else None,
        )
    finally:
        deps.store.close()
        deps.client.close()
    _print_dataset(dataset)
    return 0


def _fmt_span(span: tuple[int, int] | None) -> str:
    if span is None:
        return "n/d"
    start = datetime.fromtimestamp(span[0] / 1000, tz=UTC)
    end = datetime.fromtimestamp(span[1] / 1000, tz=UTC)
    days = (span[1] - span[0]) / 86_400_000
    return f"{start:%Y-%m-%d %H:%M}Z .. {end:%Y-%m-%d %H:%M}Z  ({days:.2f} dias)"


def _print_campaign(campaign: ExperimentCampaign) -> None:
    request = campaign.request
    out = sys.stdout.write
    out(
        "PLANO EXPERIMENTAL DE ARQUITETURA ESTATISTICA (Stage C nao executado)\n\n"
        f"  Filtro: partition={request.partition} "
        f"difficulties={sorted(request.difficulties)}\n\n"
        "  Disponibilidade\n"
        f"    observacoes candidatas ........... {campaign.candidates_available}\n"
        f"    estratos (spec x encounter x faixa) {campaign.strata_total}\n\n"
        "  Amostra planejada\n"
        f"    observacoes ...................... {campaign.n_observations}\n"
        f"    estratos cobertos ................ {campaign.strata_covered}"
        f" / {campaign.strata_total}\n"
        f"    fights distintos ................. {campaign.distinct_fights}\n"
        f"    reports distintos ................ {campaign.distinct_reports}\n"
        f"    Phase4Targets envolvidos ......... {len(campaign.by_target)}\n"
        f"    specs ............................ {len(campaign.by_spec)}\n"
        f"    encounters ....................... {len(campaign.by_encounter)}\n"
        f"    cobertura temporal ............... {_fmt_span(campaign.temporal_span_ms)}\n"
        f"    motivo de parada ................. {campaign.stopped_reason}\n\n"
    )
    out("  Distribuicao por faixa de rankPercent\n")
    for bucket in PERCENTILE_BUCKETS:
        out(f"    {bucket} ......................... {campaign.by_bucket.get(bucket, 0)}\n")

    out("\n  Distribuicao por spec\n")
    for spec, count in sorted(campaign.by_spec.items(), key=lambda kv: (-kv[1], kv[0])):
        out(f"    {spec:<28} {count}\n")

    out("\n  Distribuicao por encounter\n")
    for encounter, count in sorted(campaign.by_encounter.items()):
        out(f"    {encounter} ............................ {count}\n")

    budget = request.budget
    remaining = budget.max_api_points - campaign.estimated_api_points
    out(
        "\n  Orcamento de API\n"
        f"    custo estimado do Stage C ........ {campaign.estimated_api_points:.0f} pontos\n"
        f"    teto declarado ................... {budget.max_api_points:.0f} pontos\n"
        f"    restante sob o teto .............. {remaining:.0f} pontos\n"
    )
    if campaign.n_observations:
        per_observation = campaign.estimated_api_points / campaign.n_observations
        out(f"    custo por observacao ............. {per_observation:.1f} pontos\n")
    ok = campaign.meets_minimum_coverage
    out(
        f"\n  Cobertura minima para S3/S4 "
        f"(>= {MIN_ENCOUNTERS_FOR_EXPERIMENT} encounters, "
        f">= {MIN_SPECS_FOR_EXPERIMENT} specs): {'OK' if ok else 'INSUFICIENTE'}\n"
        "\nNENHUMA COLETA FOI EXECUTADA. Este comando apenas planeja.\n"
    )


def _print_dataset(dataset: ExperimentalFeatureDataset) -> None:
    out = sys.stdout.write
    out(
        "STATUS DA AMOSTRA EXPERIMENTAL\n\n"
        f"  observacoes coletadas ............... {len(dataset)}\n"
        f"  ignoradas (log ilegivel/incompleto) . {dataset.skipped_incomplete}\n"
        f"  specs ............................... {len(dataset.specs)}\n"
        f"  encounters .......................... {len(dataset.encounters)}\n"
        f"  Phase4Targets ....................... {len(dataset.targets)}\n"
        f"  cobertura temporal .................. {_fmt_span(dataset.temporal_span_ms)}\n\n"
    )
    if dataset.is_empty:
        out(
            "  Nenhuma observacao experimental coletada ainda.\n"
            "  Rode 'experiment-plan' e submeta o plano para aprovacao antes de coletar.\n"
        )
        return

    enough_encounters = len(dataset.encounters) >= MIN_ENCOUNTERS_FOR_EXPERIMENT
    enough_specs = len(dataset.specs) >= MIN_SPECS_FOR_EXPERIMENT
    out(
        "  Construibilidade dos splits\n"
        "    S1 temporal within-target ......... "
        f"{'OK' if len(dataset) >= 2 else 'dados insuficientes'}\n"
        "    S2 held-out logs/players .......... "
        f"{'OK' if len(dataset) >= 2 else 'dados insuficientes'}\n"
        "    S3 held-out encounter ............. "
        f"{'OK' if enough_encounters else f'precisa de >= {MIN_ENCOUNTERS_FOR_EXPERIMENT}'}\n"
        "    S4 held-out spec .................. "
        f"{'OK' if enough_specs else f'precisa de >= {MIN_SPECS_FOR_EXPERIMENT}'}\n"
        "    S5 held-out spec+encounter ........ "
        f"{'OK' if enough_encounters and enough_specs else 'dados insuficientes'}\n"
        "\nNenhum modelo foi treinado.\n"
    )
