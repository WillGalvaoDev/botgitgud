"""EB.4 — incremental, resumable Encounter Benchmark construction.

Mirrors `analysis/cohort_increment.py`'s proven shape (checkpoint, batch
loop, budget re-check per batch, BUDGET vs NO_PROGRESS distinction), with
one structural difference the ticket asked for explicitly: `NO_PROGRESS`
here is a peer return STATE, not a `defer_reason` folded under
`DEFERRED_BUDGET` — `advance_benchmark_build` can return exactly one of
READY / DEFERRED_BUDGET / NO_PROGRESS / FAILED.

Two-tier cache reuse, cheapest first:
  1. **`Store.read_log()`** — if a candidate already has a fully-fetched
     `PlayerLog` with `build.setup is not None` sitting in the existing
     warehouse (someone already ran a real analysis over that exact log),
     reuse it wholesale: zero network cost, and it hands over BOTH
     `SetupProfile` and the real `rank_percent` (the full pipeline's own
     percentile fetch) for free. A `setup=None` old log (pre-EB.0) does NOT
     count as a hit — it needs the fetch below like anything else.
  2. **Fight-wide setup-only fetch** — for whatever's still pending, group
     by `(report_code, fight_id)` and spend at most 2 queries per unique
     fight (`report.rankings` for `rank_percent`/`partition`,
     `QUERY_PLAYER_SETUP_ONLY` for `combatantInfo`) regardless of how many
     candidates share that fight.

Neither path ever constructs a real `PlayerLog` row in the `logs` table —
a benchmark observation from path 2 is a THIN, in-memory-only `PlayerLog`
(just the fields `analysis/benchmark_aggregate.py`'s aggregator reads:
fight identity/dims, `build.class_name/spec_name/item_level/setup`,
`percentile`) built fresh at finalization time from
`benchmark_build_progress` rows, never written to Parquet/`Store.write_log`.
Writing an incomplete `PlayerLog` over a real one would silently corrupt
the warehouse's own contract — this never happens.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import structlog

from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget
from botgitgud.analysis.benchmark_aggregate import build_encounter_benchmark
from botgitgud.analysis.benchmark_build_budget import affordable_fights, estimate_benchmark_build
from botgitgud.analysis.benchmark_build_progress import BenchmarkBuildProgressStore, ProgressRow
from botgitgud.analysis.benchmark_store import BenchmarkStore
from botgitgud.config import Settings
from botgitgud.domain.models import FightRef, PlayerBuild, PlayerLog, RankingCandidate
from botgitgud.ingest.benchmark_fetch import QueryFn, fetch_fight_player_details
from botgitgud.ingest.fight_rankings import DpsRanking, FightRankings, fetch_fight_rankings
from botgitgud.ingest.store import Store
from botgitgud.ingest.wcl_parsing import find_player_in_details

log = structlog.get_logger(__name__)


class BenchmarkBuildState(StrEnum):
    READY = "ready"
    DEFERRED_BUDGET = "deferred_budget"
    NO_PROGRESS = "no_progress"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class BenchmarkBuildResult:
    benchmark_id: str
    state: BenchmarkBuildState
    planned: int
    completed: int
    remaining: int
    planned_by_band: dict[str, int]
    completed_by_band: dict[str, int]
    remaining_by_band: dict[str, int]
    newly_fetched: int
    cache_hits: int
    queries_estimated: float
    points_estimated: float
    failure_reason: str | None = None

    @property
    def is_ready(self) -> bool:
        return self.state is BenchmarkBuildState.READY


_UNKNOWN_BAND = "pending"


def _band_counts(rows: Sequence[ProgressRow]) -> tuple[dict[str, int], dict[str, int]]:
    """`completed`: rows já resolvidas (`fetched`), agrupadas pela banda real
    conhecida. `remaining`: rows ainda sem banda conhecida (`pending`/
    `failed` — uma falha permanente também não tem banda, e nunca vira
    `READY` sozinha), todas sob a chave `"pending"` — nunca inventada.
    """
    completed: dict[str, int] = {}
    remaining: dict[str, int] = {}
    for r in rows:
        if r.status == "fetched" and r.band is not None:
            completed[r.band] = completed.get(r.band, 0) + 1
        else:
            remaining[_UNKNOWN_BAND] = remaining.get(_UNKNOWN_BAND, 0) + 1
    return completed, remaining


def _planned_by_band(
    completed_by_band: dict[str, int], remaining_by_band: dict[str, int]
) -> dict[str, int]:
    planned: dict[str, int] = dict(completed_by_band)
    for band, n in remaining_by_band.items():
        planned[band] = planned.get(band, 0) + n
    return planned


def _thin_player_log(row: ProgressRow, target: EncounterBenchmarkTarget) -> PlayerLog:
    """Nunca persistido — só o que `benchmark_aggregate.build_encounter_benchmark`
    de fato lê fora de `dps`/`cast_timeline`, que ficam com seus defaults
    inertes.
    """
    return PlayerLog(
        fight=FightRef(
            report_code=row.report_code,
            fight_id=row.fight_id,
            encounter_id=target.encounter_id,
            boss_name="",
            difficulty=target.difficulty,
            duration_s=row.duration_s or 0.0,
            kill=True,
            partition=target.partition,
        ),
        build=PlayerBuild(
            character_name=row.player_name,
            server=row.server,
            class_name=row.class_name or target.spec.class_name,
            spec_name=row.spec_name or target.spec.spec_name,
            role="dps",
            item_level=row.item_level,
            talent_hash=None,
            tier_pieces=None,
            setup=row.setup,
        ),
        dps=None,
        percentile=row.rank_percent,
        cast_timeline={},
    )


def _reuse_from_log_store(store: Store, candidate: RankingCandidate) -> PlayerLog | None:
    """Nível 1 de cache — o mais barato possível: um `PlayerLog` real já
    analisado, com `setup` já presente. `setup=None` (log pré-EB.0) NÃO
    conta como hit; segue para o fetch fight-wide.
    """
    existing = store.read_log(candidate.report_code, candidate.fight_id, candidate.player_name)
    if existing is not None and existing.build.setup is not None:
        return existing
    return None


def _find_dps_ranking(rankings: FightRankings | None, player_name: str) -> DpsRanking | None:
    if rankings is None:
        return None
    target_lower = player_name.strip().lower()
    for entry in rankings.dps:
        if entry.player_name.strip().lower() == target_lower:
            return entry
    return None


def _run_local_cache_pass(
    *,
    progress_store: BenchmarkBuildProgressStore,
    log_store: Store,
    benchmark_id: str,
    policy: BenchmarkPolicy,
    pending: list[ProgressRow],
) -> tuple[list[ProgressRow], int]:
    """Nível 1: reaproveita `PlayerLog`s já completos do warehouse existente
    — zero rede. Roda ANTES de qualquer decisão de orçamento, porque não
    consome orçamento nenhum.
    """
    cache_hits = 0
    still_pending: list[ProgressRow] = []
    for row in pending:
        candidate = RankingCandidate(
            row.report_code, row.fight_id, row.player_name, row.duration_s or 0.0
        )
        reused = _reuse_from_log_store(log_store, candidate)
        if reused is None:
            still_pending.append(row)
            continue
        band = policy.band_for(reused.percentile)
        progress_store.mark_fetched(
            benchmark_id,
            row.report_code,
            row.fight_id,
            row.player_name,
            band=band.name if band is not None else "outside_policy_bands",
            rank_percent=reused.percentile,
            duration_s=reused.fight.duration_s,
            item_level=reused.build.item_level,
            class_name=reused.build.class_name,
            spec_name=reused.build.spec_name,
            server=reused.build.server,
            partition=reused.fight.partition,
            setup=reused.build.setup,
        )
        cache_hits += 1
    return still_pending, cache_hits


def _run_network_batch(
    *,
    query_fn: QueryFn,
    progress_store: BenchmarkBuildProgressStore,
    benchmark_id: str,
    policy: BenchmarkPolicy,
    fights: list[tuple[str, int]],
    candidates_by_fight: dict[tuple[str, int], list[str]],
) -> int:
    """Um lote de fights únicos: no máximo 2 queries por fight, sem importar
    quantos candidatos aquele fight sirva. Devolve quantos candidatos foram
    resolvidos com sucesso (`fetched`) neste lote.
    """
    newly_fetched = 0
    for report_code, fight_id in fights:
        rankings = fetch_fight_rankings(query_fn, report_code=report_code, fight_id=fight_id)
        player_details = fetch_fight_player_details(
            query_fn, report_code=report_code, fight_id=fight_id
        )
        for player_name in candidates_by_fight[(report_code, fight_id)]:
            if player_details is None:
                progress_store.mark_failed(
                    benchmark_id,
                    report_code,
                    fight_id,
                    player_name,
                    error="setup-only fetch failed for this fight",
                )
                continue
            match = find_player_in_details(player_details, player_name)
            dps_ranking = _find_dps_ranking(rankings, player_name)
            if match is None or dps_ranking is None or dps_ranking.rank_percent is None:
                progress_store.mark_failed(
                    benchmark_id,
                    report_code,
                    fight_id,
                    player_name,
                    error="player not found in playerDetails/rankings, or missing rank_percent",
                )
                continue
            band = policy.band_for(dps_ranking.rank_percent)
            # duration_s é propriedade do FIGHT, não do jogador — DpsRanking
            # não carrega o campo; toda a plateia do mesmo fight compartilha
            # o mesmo rankings.duration_s.
            progress_store.mark_fetched(
                benchmark_id,
                report_code,
                fight_id,
                player_name,
                band=band.name if band is not None else "outside_policy_bands",
                rank_percent=dps_ranking.rank_percent,
                duration_s=rankings.duration_s if rankings is not None else None,
                item_level=match.item_level,
                class_name=match.class_name,
                spec_name=match.spec_name,
                server=match.server,
                partition=rankings.partition if rankings is not None else None,
                setup=match.setup,
            )
            newly_fetched += 1
    return newly_fetched


def _available_points(client: object) -> float:
    refresh = getattr(client, "refresh_budget", None)
    if callable(refresh):
        refresh()
    points = getattr(client, "points_remaining", None)
    return float(points) if isinstance(points, int | float) else 0.0


def _finalize(
    *,
    benchmark_store: BenchmarkStore,
    progress_store: BenchmarkBuildProgressStore,
    settings: Settings,
    benchmark_id: str,
    target: EncounterBenchmarkTarget,
    policy: BenchmarkPolicy,
    planned: int,
    completed: int,
    newly_fetched: int,
    cache_hits: int,
) -> BenchmarkBuildResult:
    rows = progress_store.read_progress(benchmark_id)
    fetched_rows = [r for r in rows if r.status == "fetched"]
    observations = [_thin_player_log(r, target) for r in fetched_rows]
    benchmark = build_encounter_benchmark(observations, target=target, policy=policy)
    benchmark_store.write_benchmark(benchmark, policy=policy, observations=observations)

    completed_by_band, remaining_by_band = _band_counts(rows)
    log.info(
        "benchmark_builder.ready",
        benchmark_id=benchmark_id,
        planned=planned,
        completed=completed,
        fetched_rows=len(fetched_rows),
    )
    return BenchmarkBuildResult(
        benchmark_id=benchmark_id,
        state=BenchmarkBuildState.READY,
        planned=planned,
        completed=completed,
        remaining=0,
        planned_by_band=_planned_by_band(completed_by_band, remaining_by_band),
        completed_by_band=completed_by_band,
        remaining_by_band={},
        newly_fetched=newly_fetched,
        cache_hits=cache_hits,
        queries_estimated=0.0,
        points_estimated=0.0,
    )


def advance_benchmark_build(
    *,
    query_fn: QueryFn,
    client: object,
    log_store: Store,
    progress_store: BenchmarkBuildProgressStore,
    benchmark_store: BenchmarkStore,
    settings: Settings,
    target: EncounterBenchmarkTarget,
    policy: BenchmarkPolicy,
    candidates: Sequence[RankingCandidate],
    max_batches: int | None = None,
) -> BenchmarkBuildResult:
    """Uma janela: avança o quanto o orçamento ATUAL permitir, então devolve.
    `RateLimitBudgetExceeded` continua propagando (mesmo contrato de
    `advance_cohort_build`) — é o backstop do próprio cliente, não uma
    decisão deste motor.

    `max_batches` (EB.5): teto de EQUIDADE, não de orçamento — `None`
    (padrão, comportamento inalterado de EB.4) deixa o laço avançar até o
    orçamento OU o trabalho acabarem, como sempre. Um chamador de fila
    (job de baixa prioridade) passa um valor pequeno (tipicamente 1) para
    nunca monopolizar o worker por múltiplos lotes numa única reivindicação
    — devolve `DEFERRED_BUDGET` ao atingir o teto mesmo com orçamento de
    sobra, porque do ponto de vista de quem enfileira a diferença entre
    "sem orçamento" e "sem sua vez" não importa: os dois retomam sozinhos
    depois, pelo mesmo checkpoint.
    """
    benchmark_id = target.benchmark_id
    progress_store.register_candidates(benchmark_id, candidates)
    rows = progress_store.read_progress(benchmark_id)
    planned = len(rows)
    pending = [r for r in rows if r.status == "pending"]

    pending, cache_hits = _run_local_cache_pass(
        progress_store=progress_store,
        log_store=log_store,
        benchmark_id=benchmark_id,
        policy=policy,
        pending=pending,
    )

    if not pending:
        return _finalize(
            benchmark_store=benchmark_store,
            progress_store=progress_store,
            settings=settings,
            benchmark_id=benchmark_id,
            target=target,
            policy=policy,
            planned=planned,
            completed=planned,
            newly_fetched=0,
            cache_hits=cache_hits,
        )

    newly_fetched = 0
    attempted_fights: set[tuple[str, int]] = set()
    state: BenchmarkBuildState | None = None
    batches_run = 0

    while pending:
        candidates_by_fight: dict[tuple[str, int], list[str]] = {}
        for r in pending:
            candidates_by_fight.setdefault(r.fight_key, []).append(r.player_name)
        untried_fights = [f for f in candidates_by_fight if f not in attempted_fights]
        if not untried_fights:
            state = BenchmarkBuildState.NO_PROGRESS
            log.warning(
                "benchmark_builder.no_fetchable_fight_left",
                benchmark_id=benchmark_id,
                remaining=len(pending),
            )
            break

        available = _available_points(client)
        affordable = affordable_fights(settings, available, planned=len(untried_fights))
        if affordable <= 0:
            state = BenchmarkBuildState.DEFERRED_BUDGET
            break

        batch = untried_fights[: min(affordable, settings.benchmark_build_chunk_fights)]
        newly_fetched += _run_network_batch(
            query_fn=query_fn,
            progress_store=progress_store,
            benchmark_id=benchmark_id,
            policy=policy,
            fights=batch,
            candidates_by_fight=candidates_by_fight,
        )
        attempted_fights.update(batch)
        batches_run += 1

        rows = progress_store.read_progress(benchmark_id)
        pending = [r for r in rows if r.status == "pending"]

        if pending and max_batches is not None and batches_run >= max_batches:
            state = BenchmarkBuildState.DEFERRED_BUDGET
            log.info(
                "benchmark_builder.batch_cap_reached",
                benchmark_id=benchmark_id,
                batches_run=batches_run,
                remaining=len(pending),
            )
            break

    if not pending:
        return _finalize(
            benchmark_store=benchmark_store,
            progress_store=progress_store,
            settings=settings,
            benchmark_id=benchmark_id,
            target=target,
            policy=policy,
            planned=planned,
            completed=planned,
            newly_fetched=newly_fetched,
            cache_hits=cache_hits,
        )

    assert state is not None
    rows = progress_store.read_progress(benchmark_id)
    completed = planned - len(pending)
    completed_by_band, remaining_by_band = _band_counts(rows)
    remaining_fights = {r.fight_key for r in pending}
    cost = estimate_benchmark_build(settings, 0.0, fights=len(remaining_fights))
    log.warning(
        "benchmark_builder.deferred",
        benchmark_id=benchmark_id,
        state=str(state),
        planned=planned,
        completed=completed,
        remaining=len(pending),
        newly_fetched=newly_fetched,
        cache_hits=cache_hits,
    )
    return BenchmarkBuildResult(
        benchmark_id=benchmark_id,
        state=state,
        planned=planned,
        completed=completed,
        remaining=len(pending),
        planned_by_band=_planned_by_band(completed_by_band, remaining_by_band),
        completed_by_band=completed_by_band,
        remaining_by_band=remaining_by_band,
        newly_fetched=newly_fetched,
        cache_hits=cache_hits,
        queries_estimated=cost.estimated_queries_upper,
        points_estimated=cost.estimated_upper_bound,
        failure_reason=None,
    )


def build_benchmark_until_budget(
    *,
    query_fn: QueryFn,
    client: object,
    log_store: Store,
    progress_store: BenchmarkBuildProgressStore,
    benchmark_store: BenchmarkStore,
    settings: Settings,
    target: EncounterBenchmarkTarget,
    policy: BenchmarkPolicy,
    candidates: Sequence[RankingCandidate],
    max_windows: int = 1000,
) -> BenchmarkBuildResult:
    """Conveniência: chama `advance_benchmark_build` repetidamente até um
    estado terminal (READY, ou uma janela que não progrediu:
    DEFERRED_BUDGET/NO_PROGRESS/FAILED). Nunca insiste depois de uma janela
    sem progresso algum — um `client`/`query_fn` estático nunca vai
    progredir sozinho numa segunda chamada; quem decide reconsiderar
    orçamento é o chamador (job/worker futuro), não este loop.
    """
    result: BenchmarkBuildResult | None = None
    for _ in range(max_windows):
        result = advance_benchmark_build(
            query_fn=query_fn,
            client=client,
            log_store=log_store,
            progress_store=progress_store,
            benchmark_store=benchmark_store,
            settings=settings,
            target=target,
            policy=policy,
            candidates=candidates,
        )
        if result.state is not BenchmarkBuildState.READY and (
            result.newly_fetched > 0 or result.cache_hits > 0
        ):
            continue
        return result
    assert result is not None
    return result
