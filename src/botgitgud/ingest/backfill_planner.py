"""Read-only multi-target planner for Stage C; it never calls LogFetcher."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from botgitgud.ingest.store import Store
from botgitgud.phase4.target import Phase4Target

ESTIMATED_POINTS_PER_OBSERVATION = 17.0


@dataclass(frozen=True, slots=True)
class BackfillPlan:
    target: Phase4Target
    required_observations: int = 5000

    def __post_init__(self) -> None:
        if self.required_observations <= 0:
            raise ValueError("required_observations must be positive")


@dataclass(frozen=True, slots=True)
class PlannedObservation:
    report_code: str
    fight_id: int
    player_name: str


@dataclass(frozen=True, slots=True)
class BackfillEstimate:
    plan: BackfillPlan
    candidate_observations: int
    already_ingested: int
    observations_remaining: int
    observations_planned: tuple[PlannedObservation, ...]
    estimated_api_points: float
    coverage_start: datetime | None
    coverage_end: datetime | None


class BackfillPlanner:
    def __init__(self, store: Store) -> None:
        self._store = store

    def plan(self, request: BackfillPlan) -> BackfillEstimate:
        target = request.target
        params = {
            "c": target.spec.class_name,
            "s": target.spec.spec_name,
            "e": target.encounter_id,
            "d": target.difficulty,
            "p": target.partition,
        }
        candidates = self._store.query(
            """
            SELECT dt.report_code, dt.fight_id, dt.player_name, dr.start_time_ms
            FROM discovery_targets dt
            JOIN discovery_fights df USING (report_code, fight_id)
            JOIN discovery_reports dr USING (report_code)
            WHERE dt.class_name=$c AND dt.spec_name=$s AND df.encounter_id=$e
              AND df.difficulty=$d AND df.partition=$p AND df.kill=true
            ORDER BY dr.start_time_ms, dt.report_code, dt.fight_id, dt.player_name
            """,
            **params,
        )
        ingested = self._store.query(
            "SELECT count(DISTINCT (report_code, fight_id, player_name)) AS n FROM logs "
            "WHERE class_name=$c AND spec_name=$s AND encounter_id=$e "
            "AND difficulty=$d AND partition=$p",
            **params,
        )
        already_ingested = int(ingested["n"][0])
        remaining = max(0, request.required_observations - already_ingested)
        missing: list[PlannedObservation] = []
        for row in candidates.iter_rows(named=True):
            exists = self._store.query(
                "SELECT 1 FROM logs WHERE report_code=$r AND fight_id=$f "
                "AND player_name=$n LIMIT 1",
                r=row["report_code"],
                f=row["fight_id"],
                n=row["player_name"],
            )
            if len(exists) == 0 and len(missing) < remaining:
                missing.append(
                    PlannedObservation(row["report_code"], row["fight_id"], row["player_name"])
                )
        times = [int(row["start_time_ms"]) for row in candidates.iter_rows(named=True)]
        return BackfillEstimate(
            plan=request,
            candidate_observations=len(candidates),
            already_ingested=already_ingested,
            observations_remaining=remaining,
            observations_planned=tuple(missing),
            estimated_api_points=len(missing) * ESTIMATED_POINTS_PER_OBSERVATION,
            coverage_start=datetime.fromtimestamp(min(times) / 1000) if times else None,
            coverage_end=datetime.fromtimestamp(max(times) / 1000) if times else None,
        )
