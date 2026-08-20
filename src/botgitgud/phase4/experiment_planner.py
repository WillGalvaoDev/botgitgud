"""SAE.2 — read-only, deterministic multi-target sampler for the experimental
Stage C campaign (docs/fase4-statistical-architecture-experiment.md §6-§7).

Produces a campaign; it never executes it. Like ingest/backfill_planner.py
it takes only a `Store` — no `WclClient`, no `LogFetcher` — so it cannot
issue a network call even by accident.

Two properties matter more than sample size here:

* **Determinism.** No RNG anywhere. Strata are visited in canonical order
  and picks inside a stratum follow a fixed binary-subdivision sequence, so
  the same warehouse always yields the same campaign and a reviewer can
  reproduce it exactly.
* **Width over depth.** The Fase 4 gate wants 5.000 observations of one
  target; this experiment wants a few hundred spread over as many
  specs/encounters/percentile bands as possible, because S3/S4 (held-out
  encounter/spec) are impossible without that spread.

Partition is a hard single value (§1.5: never mix partitions) and difficulty
is an explicit set, so neither can be silently blended into the sample.
"""

from __future__ import annotations

from collections import deque

from botgitgud.domain.specs import SpecId, SpecSupport, classify_spec
from botgitgud.ingest.store import Store
from botgitgud.phase4.experiment import percentile_bucket
from botgitgud.phase4.experiment_campaign import (
    ExperimentCampaign,
    PlannedExperimentObservation,
    StatisticalExperimentPlan,
    StopReason,
)

_StratumKey = tuple[str, int, str]  # (spec key, encounter_id, percentile bucket)


def spread_order(n: int) -> list[int]:
    """A permutation of range(n) whose every prefix is spread evenly across
    the range (recursive midpoints, breadth-first).

    Taking the first k of a time-ordered stratum would pile the sample into
    the start of the census window and make S1's temporal split degenerate;
    taking `spread_order`'s first k samples the window evenly for any k,
    without needing to know k in advance.
    """
    if n <= 0:
        return []
    order: list[int] = []
    queue: deque[tuple[int, int]] = deque([(0, n - 1)])
    while queue:
        lo, hi = queue.popleft()
        if lo > hi:
            continue
        mid = (lo + hi) // 2
        order.append(mid)
        queue.append((lo, mid - 1))
        queue.append((mid + 1, hi))
    return order


class ExperimentPlanner:
    """Read-only: `Store` is the only collaborator, mirroring
    ingest/backfill_planner.py. No client is reachable from here.
    """

    def __init__(self, store: Store) -> None:
        self._store = store

    def plan(self, request: StatisticalExperimentPlan) -> ExperimentCampaign:
        candidates = self._load_candidates(request)
        strata = _build_strata(candidates)
        observations, points, stopped = _allocate(
            strata, request=request, pool_size=len(candidates)
        )
        covered = {(o.spec_key, o.encounter_id, o.bucket) for o in observations}
        return ExperimentCampaign(
            request=request,
            observations=tuple(observations),
            candidates_available=len(candidates),
            strata_total=len(strata),
            strata_covered=len(covered),
            estimated_api_points=points,
            stopped_reason=stopped,
        )

    def _load_candidates(
        self, request: StatisticalExperimentPlan
    ) -> list[PlannedExperimentObservation]:
        rows = self._store.query(
            """
            SELECT dt.report_code, dt.fight_id, dt.player_name,
                   dt.class_name, dt.spec_name, dt.rank_percent,
                   df.encounter_id, df.difficulty, df.partition,
                   dr.start_time_ms
            FROM discovery_targets dt
            JOIN discovery_fights df USING (report_code, fight_id)
            JOIN discovery_reports dr USING (report_code)
            WHERE df.kill = true
              AND df.partition = $p
              AND dt.rank_percent IS NOT NULL
            ORDER BY dr.start_time_ms, dt.report_code, dt.fight_id, dt.player_name
            """,
            p=request.partition,
        )
        out: list[PlannedExperimentObservation] = []
        for row in rows.iter_rows(named=True):
            candidate = _row_to_candidate(row, request)
            if candidate is not None:
                out.append(candidate)
        return out


def _row_to_candidate(
    row: dict[str, object], request: StatisticalExperimentPlan
) -> PlannedExperimentObservation | None:
    if row["difficulty"] not in request.difficulties:
        return None
    if request.encounters is not None and row["encounter_id"] not in request.encounters:
        return None
    spec = SpecId(str(row["class_name"]), str(row["spec_name"]))
    if classify_spec(spec) is not SpecSupport.SUPPORTED:
        return None
    if request.specs is not None and spec not in request.specs:
        return None
    rank_percent = float(row["rank_percent"])  # type: ignore[arg-type]
    if not 0.0 <= rank_percent <= 100.0:
        return None
    return PlannedExperimentObservation(
        report_code=str(row["report_code"]),
        fight_id=int(row["fight_id"]),  # type: ignore[arg-type]
        player_name=str(row["player_name"]),
        class_name=spec.class_name,
        spec_name=spec.spec_name,
        encounter_id=int(row["encounter_id"]),  # type: ignore[arg-type]
        difficulty=int(row["difficulty"]),  # type: ignore[arg-type]
        partition=int(row["partition"]),  # type: ignore[arg-type]
        rank_percent=rank_percent,
        bucket=percentile_bucket(rank_percent),
        start_time_ms=int(row["start_time_ms"]),  # type: ignore[arg-type]
    )


def _build_strata(
    candidates: list[PlannedExperimentObservation],
) -> dict[_StratumKey, list[PlannedExperimentObservation]]:
    strata: dict[_StratumKey, list[PlannedExperimentObservation]] = {}
    for candidate in candidates:
        key = (candidate.spec_key, candidate.encounter_id, candidate.bucket)
        strata.setdefault(key, []).append(candidate)
    return strata


def balanced_visit_order(keys: list[_StratumKey]) -> list[_StratumKey]:
    """Order strata so that ANY prefix is balanced across all three
    dimensions at once.

    Plain `sorted()` would be a trap: it groups by spec first, so a sample
    smaller than the number of strata would contain only the
    alphabetically-first specs and silently defeat the whole point of
    stratifying. Instead this emits in levels — level L gives every spec its
    (L+1)-th stratum — and inside a level picks each spec's least-used
    encounter and bucket, so encounters and percentile bands stay balanced
    too. The per-level rotation of spec order keeps one spec from always
    winning a truncation boundary. Fully deterministic.
    """
    by_spec: dict[str, list[_StratumKey]] = {}
    for key in sorted(keys):
        by_spec.setdefault(key[0], []).append(key)

    specs = sorted(by_spec)
    used: set[_StratumKey] = set()
    encounter_counts: dict[int, int] = {}
    bucket_counts: dict[str, int] = {}
    order: list[_StratumKey] = []

    deepest = max((len(v) for v in by_spec.values()), default=0)
    for level in range(deepest):
        rotation = level % len(specs) if specs else 0
        for spec in specs[rotation:] + specs[:rotation]:
            remaining = [k for k in by_spec[spec] if k not in used]
            if not remaining:
                continue
            chosen = min(
                remaining,
                key=lambda k: (encounter_counts.get(k[1], 0), bucket_counts.get(k[2], 0), k),
            )
            used.add(chosen)
            order.append(chosen)
            encounter_counts[chosen[1]] = encounter_counts.get(chosen[1], 0) + 1
            bucket_counts[chosen[2]] = bucket_counts.get(chosen[2], 0) + 1
    return order


def _allocate(
    strata: dict[_StratumKey, list[PlannedExperimentObservation]],
    *,
    request: StatisticalExperimentPlan,
    pool_size: int,
) -> tuple[list[PlannedExperimentObservation], float, StopReason]:
    """Round-robin over balance-ordered strata: no stratum is served a
    second time before every stratum has been served once, which is what
    keeps popular specs and top parses from dominating the sample.
    """
    ordered_keys = balanced_visit_order(list(strata))
    orders = {key: spread_order(len(strata[key])) for key in ordered_keys}
    budget = request.budget
    cap = request.observation_cap(pool_size)
    # Hitting a cap that came from the pool itself is exhaustion, not a
    # caller-imposed limit — reporting it as `max_observations` would be a lie.
    cap_reason: StopReason = (
        "max_observations" if request.max_observations is not None else "pool_exhausted"
    )

    selected: list[PlannedExperimentObservation] = []
    fights: set[tuple[str, int]] = set()
    points = 0.0
    round_index = 0

    while True:
        progressed = False
        for key in ordered_keys:
            if len(selected) >= cap:
                return selected, points, cap_reason
            order = orders[key]
            if round_index >= len(order):
                continue
            candidate = strata[key][order[round_index]]
            is_new_fight = candidate.fight_key not in fights
            delta = budget.points_per_player_marginal + (
                budget.points_per_fight_shared if is_new_fight else 0.0
            )
            if points + delta > budget.max_api_points:
                return selected, points, "budget_exhausted"
            selected.append(candidate)
            fights.add(candidate.fight_key)
            points += delta
            progressed = True
        if not progressed:
            return selected, points, "pool_exhausted"
        round_index += 1
