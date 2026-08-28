"""T1.6/T1.7 — the analysis orchestrator, replacing bot.py's run_analysis.

`Deps` is a plain container of injected dependencies (client, fetcher,
store, catalog, settings) — no singletons, no module-level globals (§1.3).
Every failure path is a specific exception from errors.py; the boundary
layers (bot/discord_bot.py, cli.py) are the only places allowed to catch
them broadly and translate to a user-facing message.

T1.7: the cohort is now identity-addressed by CohortCriteria/cohort_id
(T1.5) and its candidate pool looked up in the Store before ever hitting
the live rankings API. `allow_cold_build=False` (used by the Discord path)
turns a cache miss into CohortNotReady instead of a synchronous 100-log
fetch — "o caminho interativo... nunca baixa 100 logs de forma síncrona".

T2.1 (docs/desvios.md D-25): the Store now caches the raw candidate pool
per cohort_id instead of a pre-aggregated CohortProfile — per-player
covariate matching (analysis/cohort_match.py) means the aggregate can't be
precomputed once and shared across every player who lands in the same
duration bucket, but the candidate *pool* (which report/fight/player this
bucket's rankings query resolved to) is identical for all of them, so a
warm request still spends zero characterRankings queries. Every request,
warm or cold, ends the same way: fetch_cohort_logs -> match_cohort ->
build_cd_reference_profile, freshly, against whichever reference logs
survive THIS player's own matching cascade.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

import structlog

from botgitgud import telemetry
from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget
from botgitgud.analysis.benchmark_store import BenchmarkStore
from botgitgud.analysis.benchmark_store_models import EncounterBenchmarkCorruptPayloadError
from botgitgud.analysis.cohort import (
    COHORT_MIN_HARD,
    POSITIONAL_MIN_N,
    classify_cohort_size,
    duration_bucket_bounds,
    duration_bucket_id,
)
from botgitgud.analysis.cohort_increment import CohortState, advance_cohort_build
from botgitgud.analysis.cohort_match import match_cohort
from botgitgud.analysis.cold_build import (
    ColdBuildExecution,
    ColdBuildMode,
    budget_reset_in,
    cohort_single_flight,
    estimate_cold_build,
    preflight_cold_build,
)
from botgitgud.analysis.comparison import SpellComparison, compare_all_spells
from botgitgud.analysis.dps_gap import DpsGapReport, analyze_dps_gap
from botgitgud.analysis.findings import Finding, build_findings, select_top_actions
from botgitgud.analysis.performance_features import (
    PerformanceFindings,
    analyze_performance_features,
)
from botgitgud.analysis.profile import build_cd_reference_profile, discover_eligible_spell_ids
from botgitgud.analysis.setup_analysis import SetupAnalysis, analyze_setup
from botgitgud.config import Settings
from botgitgud.domain.models import CohortCriteria, RankingCandidate, RunManifest
from botgitgud.domain.specs import SpecId, SpecSupport, classify_spec, rejection_message
from botgitgud.domain.spells import SpellCatalog
from botgitgud.errors import (
    CohortDeferredBudget,
    CohortNotReady,
    InsufficientCohort,
    ScopeRejected,
)
from botgitgud.ingest.log_fetcher import LogFetcher
from botgitgud.ingest.rankings import (
    fetch_cohort_logs,
    fetch_ranking_candidates,
    get_current_partition,
)
from botgitgud.ingest.store import Store
from botgitgud.phase4.resolver import ModelResolution, Phase4ModelResolver, ResolutionStatus
from botgitgud.phase4.target import Phase4Target
from botgitgud.report.text import ReportHeader
from botgitgud.runmanifest import build_run_manifest
from botgitgud.wcl.client import WclClient

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Deps:
    client: WclClient
    fetcher: LogFetcher
    store: Store
    catalog: SpellCatalog
    settings: Settings
    phase4_resolver: Phase4ModelResolver | None = None


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    report_code: str
    fight_id: int
    character_name: str


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    header: ReportHeader
    comparisons: tuple[SpellComparison, ...]
    manifest: RunManifest
    # RP.2: sempre calculado (best-effort, nunca bloqueia), mas SÓ carrega
    # dado real quando um Encounter Benchmark já foi persistido para este
    # target — ver `run_analysis`. `None` só quando `analyze_setup` nunca
    # chegou a rodar (não deveria acontecer no caminho normal).
    setup_analysis: SetupAnalysis | None = None
    performance: PerformanceFindings | None = None
    dps_gap: DpsGapReport | None = None
    top_actions: tuple[Finding, ...] = ()
    reference_pool_members: int | None = None
    matched_cohort_members: int | None = None
    encounter_id: int | None = None
    difficulty: int | None = None
    # Esta execucao construiu a coorte, ou encontrou-a pronta? A telemetria nao
    # pode inferir isso pelo resultado: os dois caminhos produzem o mesmo
    # relatorio, e so o custo difere.
    cold_build_performed: bool = False
    phase4_resolution: ModelResolution = field(
        default_factory=lambda: ModelResolution(ResolutionStatus.UNAVAILABLE)
    )


def _deferred(
    deps: Deps,
    cohort_id: str,
    *,
    message: str,
    planned: int | None = None,
    completed: int | None = None,
) -> CohortDeferredBudget:
    remaining = 0 if planned is None or completed is None else planned - completed
    cost = estimate_cold_build(
        deps.settings,
        deps.client.points_remaining or 0.0,
        mode=ColdBuildMode.INTERACTIVE,
        references=remaining or 1,
    )
    return CohortDeferredBudget(
        message,
        cohort_id=cohort_id,
        estimated_api_points=cost.estimated_api_points,
        available_api_points=cost.available_api_points,
        protected_floor=cost.protected_floor,
        safety_margin=cost.safety_margin,
        planned=planned,
        completed=completed,
        retry_after_s=budget_reset_in(deps.client),
    )


def _advance_cold_cohort(
    deps: Deps,
    *,
    cohort_id: str,
    criteria: CohortCriteria,
    partition: int,
    player_log: object,
    job_id: str | None,
) -> list[RankingCandidate]:
    """Avança a coorte fria pelo MESMO motor incremental do prewarm.

    B1: o preflight pergunta se cabe UMA referência, não se cabem as 100 — as
    100 nunca cabem numa janela de 3.600 pontos, e tratar isso como "impossível"
    era o defeito. Uma janela que não completa o pool devolve
    `CohortDeferredBudget`, com o progresso já persistido no cache de logs.
    """
    fight = player_log.fight  # type: ignore[attr-defined]
    build = player_log.build  # type: ignore[attr-defined]
    preflight_cold_build(
        deps.client,
        deps.settings,
        cohort_id,
        mode=ColdBuildMode.INTERACTIVE,
        references=1,
        execution=ColdBuildExecution.RESUMABLE_INCREMENTAL,
    )
    pool = fetch_ranking_candidates(
        deps.client,
        encounter_id=fight.encounter_id,
        class_name=build.class_name,
        spec_name=build.spec_name,
        partition=partition,
        target_duration_s=fight.duration_s,
    )
    increment = advance_cohort_build(
        client=deps.client,
        fetcher=deps.fetcher,
        store=deps.store,
        settings=deps.settings,
        cohort_id=cohort_id,
        criteria=criteria,
        candidates=pool,
        partition=partition,
        mode=ColdBuildMode.INTERACTIVE,
        job_id=job_id,
    )
    if increment.state is not CohortState.READY:
        raise _deferred(
            deps,
            cohort_id,
            message=(
                "Essa análise precisa preparar uma nova coorte e o orçamento da Warcraft Logs "
                f"não cobre o restante agora ({increment.completed}/{increment.planned} logs "
                "prontos). Ela continuará automaticamente assim que houver orçamento."
            ),
            planned=increment.planned,
            completed=increment.completed,
        )
    ready = deps.store.read_candidate_pool(cohort_id)
    if ready is None:  # pragma: no cover - READY implica pool escrito
        raise CohortNotReady(f"construção da coorte {cohort_id} não foi concluída")
    return ready


def run_analysis(
    req: AnalysisRequest,
    deps: Deps,
    *,
    allow_cold_build: bool = True,
    job_id: str | None = None,
) -> AnalysisResult:
    """Raises PlayerNotFound/FightNotFound (LogFetcher.fetch), ScopeRejected
    (out-of-scope spec), CohortNotReady (no cached candidate pool and
    allow_cold_build=False), or InsufficientCohort (too few ranking
    candidates overall — raised by ingest/rankings.py, cold path only — or
    too few survive match_cohort's covariate degradation cascade, which can
    happen on either path) on every expected failure path.
    """
    # Papel explicito por fase: a mesma op_name serve jogador e referencia,
    # entao quem distingue e o momento do pipeline, nao a query.
    with telemetry.role_scope(telemetry.QueryRole.PLAYER_ANALYZED):
        player_log = deps.fetcher.fetch(req.report_code, req.fight_id, req.character_name)

    # T0.9: the scope gate runs immediately after identifying the spec,
    # BEFORE any ranking query — no API points spent on out-of-scope input.
    spec_id = SpecId(class_name=player_log.build.class_name, spec_name=player_log.build.spec_name)
    support = classify_spec(spec_id)
    if support != SpecSupport.SUPPORTED:
        message = rejection_message(support, spec_id) or "spec fora de escopo"
        raise ScopeRejected(message)

    partition = get_current_partition(deps.client, player_log.fight.encounter_id)
    phase4_target = Phase4Target(
        spec=spec_id,
        encounter_id=player_log.fight.encounter_id,
        difficulty=player_log.fight.difficulty,
        partition=partition,
    )
    phase4_resolution = (
        deps.phase4_resolver.resolve(phase4_target)
        if deps.phase4_resolver is not None
        else ModelResolution(ResolutionStatus.UNAVAILABLE)
    )

    # RP.2: Setup Analysis é INDEPENDENTE do Execution Cohort por desenho —
    # nunca usa `matched_logs`/`criteria` abaixo, nunca bloqueia a análise
    # de execução. Leitura best-effort, read-only: nenhum cold build de
    # benchmark é disparado por aqui (isso é o job de baixa prioridade de
    # EB.5 — `ensure_benchmark_job`, deliberadamente não chamado neste
    # caminho interativo). Benchmark ausente/corrompido -> `benchmark=None`
    # -> `analyze_setup` (SA.6) já degrada sozinho para findings
    # `MISSING_DATA`/`HIDDEN`, nunca uma exceção.
    benchmark_target = EncounterBenchmarkTarget(
        spec=spec_id,
        encounter_id=player_log.fight.encounter_id,
        difficulty=player_log.fight.difficulty,
        partition=partition,
    )
    benchmark_policy = BenchmarkPolicy.default()
    try:
        benchmark = BenchmarkStore(deps.store).read_benchmark(benchmark_target.benchmark_id)
    except EncounterBenchmarkCorruptPayloadError:
        log.warning("pipeline.benchmark_read_failed", benchmark_id=benchmark_target.benchmark_id)
        benchmark = None
    setup_analysis = analyze_setup(
        target=benchmark_target,
        policy=benchmark_policy,
        player_setup=player_log.build.setup,
        benchmark=benchmark,
    )

    bucket_lo, bucket_hi = duration_bucket_bounds(duration_bucket_id(player_log.fight.duration_s))
    criteria = CohortCriteria(
        encounter_id=player_log.fight.encounter_id,
        difficulty=player_log.fight.difficulty,
        partition=partition,
        class_name=player_log.build.class_name,
        spec_name=player_log.build.spec_name,
        metric="dps",
        duration_min_s=bucket_lo,
        duration_max_s=bucket_hi,
    )
    cohort_id = criteria.cohort_id()
    candidates = deps.store.read_candidate_pool(cohort_id)
    cold_build_performed = False

    if candidates is None:
        if not allow_cold_build:
            msg = (
                f"coorte ainda não construída para encontro {criteria.encounter_id} "
                f"({criteria.class_name}/{criteria.spec_name}, dif. {criteria.difficulty})"
            )
            raise CohortNotReady(msg)

        with cohort_single_flight.acquire(cohort_id) as leader:
            candidates = deps.store.read_candidate_pool(cohort_id)
            if candidates is None and leader:
                candidates = _advance_cold_cohort(
                    deps,
                    cohort_id=cohort_id,
                    criteria=criteria,
                    partition=partition,
                    player_log=player_log,
                    job_id=job_id,
                )
                cold_build_performed = True
            elif candidates is None:
                # O lider adiou. O seguidor tambem espera a MESMA construcao —
                # nunca inicia uma segunda, e nunca vira falha: seu job precisa
                # continuar elegivel exatamente como o do lider (B2).
                raise _deferred(
                    deps,
                    cohort_id,
                    message=(
                        "Essa análise depende de uma coorte que ainda está sendo preparada. "
                        "Ela continuará automaticamente assim que a preparação terminar."
                    ),
                )

    with telemetry.role_scope(telemetry.QueryRole.REFERENCE):
        reference_logs = fetch_cohort_logs(
            deps.fetcher,
            candidates,
            max_workers=deps.settings.max_workers,
            expected_partition=partition,
        )
    # EC.3: mesma matching_policy_version que já decidiu a identidade/cache
    # do candidate pool (EC.2) — nunca duas flags de versão que possam
    # divergir. `criteria` continua v1 por default (nenhum call site passa
    # outra coisa), então isto não muda comportamento nenhum hoje.
    matched_logs, match_report = match_cohort(
        player_log,
        reference_logs,
        min_n=COHORT_MIN_HARD,
        matching_policy_version=criteria.matching_policy_version,
    )
    if len(matched_logs) < COHORT_MIN_HARD:
        msg = (
            f"apenas {len(matched_logs)} logs de referência após matching de "
            f"covariáveis (mínimo: {COHORT_MIN_HARD})"
        )
        raise InsufficientCohort(msg, n_members=len(matched_logs), minimum_required=COHORT_MIN_HARD)

    # T3.1: same already-covariate-matched cohort, independent of CD timing.
    performance = analyze_performance_features(player_log, matched_logs, deps.catalog)

    profile, num_positional = build_cd_reference_profile(matched_logs, player_log.fight.duration_s)
    durations = [rl.fight.duration_s for rl in matched_logs]
    dps_values = [rl.dps for rl in matched_logs if rl.dps is not None]
    cohort_median_dps = statistics.median(dps_values) if dps_values else None
    duration_min_s = min(durations) if durations else player_log.fight.duration_s
    duration_max_s = max(durations) if durations else player_log.fight.duration_s
    cohort_size_status = classify_cohort_size(len(matched_logs))

    # T3.2: rules 1/2 of the diagnosis need to know whether a buff-related
    # covariate was relaxed — the SAME match_cohort() output T2.1 already
    # produced, not a new query.
    buffs_relaxed = bool(set(match_report.relaxed) & {"has_augmentation", "external_buffs"})
    dps_gap = analyze_dps_gap(
        player_log,
        matched_logs,
        cohort_median_dps=cohort_median_dps,
        catalog=deps.catalog,
        buffs_relaxed=buffs_relaxed,
    )
    findings = build_findings(
        dps_gap=dps_gap,
        n=num_positional,
        relaxed_covariates=match_report.relaxed,
    )
    top_actions = select_top_actions(findings)

    eligible_ids = discover_eligible_spell_ids(profile)
    comparisons = compare_all_spells(
        player_log,
        profile,
        eligible_ids,
        catalog=deps.catalog,
        reference_n=num_positional,
        gap_penalty=deps.settings.gap_penalty_s,
    )

    warnings: list[str] = []
    if cohort_size_status == "warn":
        warnings.append(
            f"Amostra pequena ({num_positional} logs). "
            "Trate os desvios como indicativos, não conclusivos."
        )
    if 0 < num_positional < POSITIONAL_MIN_N:
        warnings.append(
            f"Apenas {num_positional} logs com duração próxima à sua (±12%) para "
            "comparar o timing dos cooldowns — os valores 'Ideal' têm confiança baixa."
        )

    # T0.4/T0.7: persisted once, at the end, on the calling thread — never
    # during the concurrent reference-log fetch (achado 4.1).
    deps.catalog.flush()

    header = ReportHeader(
        char_name=player_log.build.character_name,
        boss_name=player_log.fight.boss_name,
        class_name=player_log.build.class_name,
        spec=player_log.build.spec_name,
        reference_n=num_positional,
        duration_min_s=duration_min_s,
        duration_max_s=duration_max_s,
        player_dps=player_log.dps,
        player_percentile=player_log.percentile,
        cohort_median_dps=cohort_median_dps,
        cohort_warnings=tuple(warnings),
        matched_covariates=match_report.matched,
        relaxed_covariates=match_report.relaxed,
    )
    manifest = build_run_manifest(
        cohort_id=cohort_id,
        n_members=num_positional,
        wcl_partition=partition,
        settings=deps.settings,
    )
    deps.store.write_run(manifest)

    return AnalysisResult(
        header=header,
        comparisons=tuple(comparisons),
        manifest=manifest,
        setup_analysis=setup_analysis,
        performance=performance,
        dps_gap=dps_gap,
        top_actions=tuple(top_actions),
        reference_pool_members=len(candidates),
        matched_cohort_members=len(matched_logs),
        encounter_id=player_log.fight.encounter_id,
        difficulty=player_log.fight.difficulty,
        cold_build_performed=cold_build_performed,
        phase4_resolution=phase4_resolution,
    )
