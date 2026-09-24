"""SAE.3 — the experimental feature dataset
(docs/phase4.md).

A materialization layer **separate from T4.1's final dataset**: T4.1 builds
depth for one target under the 5.000 gate, this builds width across many
targets under a much smaller budget. Keeping them apart means the experiment
cannot quietly redefine what T4.1 promised.

Read-only, no API. Rows come from the `logs` table (flat columns) joined to
each log's Parquet file through the canonical `read_parquet_log` codec, so
the nested T3.1 features are decoded exactly the way the rest of the project
decodes them.

Deduplication is by the natural key `(report_code, fight_id, player_name)`,
keeping the most recent `ingested_at` — `logs` stays insert-only (D-12c), so
this is a query-time view, never a delete.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog

from botgitgud.domain.models import PlayerLog
from botgitgud.domain.specs import SpecId
from botgitgud.ingest.parquet_codec import read_parquet_log
from botgitgud.ingest.store import Store
from botgitgud.phase4.experiment import percentile_bucket
from botgitgud.phase4.target import Phase4Target

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ExperimentalObservation:
    """One training row. Identity, context, features and target stay in
    separate fields so nothing can silently cross roles.
    """

    report_code: str
    fight_id: int
    player_name: str
    observed_at_ms: int
    target: Phase4Target
    y_rank_percent: float
    features: dict[str, float]

    @property
    def observation_key(self) -> tuple[str, int, str]:
        return (self.report_code, self.fight_id, self.player_name)

    @property
    def bucket(self) -> str:
        return percentile_bucket(self.y_rank_percent)

    @property
    def spec_key(self) -> str:
        return f"{self.target.spec.class_name}/{self.target.spec.spec_name}"


@dataclass(frozen=True, slots=True)
class ExperimentalFeatureDataset:
    observations: tuple[ExperimentalObservation, ...]
    skipped_incomplete: int = 0
    feature_schema_version: str = "experimental-features-v2"

    def __len__(self) -> int:
        return len(self.observations)

    @property
    def is_empty(self) -> bool:
        return not self.observations

    @property
    def specs(self) -> frozenset[str]:
        return frozenset(o.spec_key for o in self.observations)

    @property
    def encounters(self) -> frozenset[int]:
        return frozenset(o.target.encounter_id for o in self.observations)

    @property
    def targets(self) -> frozenset[str]:
        return frozenset(o.target.target_id for o in self.observations)

    @property
    def temporal_span_ms(self) -> tuple[int, int] | None:
        if not self.observations:
            return None
        times = [o.observed_at_ms for o in self.observations]
        return (min(times), max(times))

    def sorted_by_time(self) -> tuple[ExperimentalObservation, ...]:
        """Ties broken by the natural key so the order is total and stable —
        temporal splits must not depend on dict/query ordering.
        """
        return tuple(sorted(self.observations, key=lambda o: (o.observed_at_ms, o.observation_key)))


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def build_features(player_log: PlayerLog, *, raid_size: int | None) -> dict[str, float]:
    """Spec-agnostic aggregates only — see experiment_features.py for why
    per-spell columns are unusable across specs, and why localized
    `resource_waste` keys never become feature names.
    """
    fight, build = player_log.fight, player_log.build
    duration_s = fight.duration_s or 0.0
    minutes = duration_s / 60.0

    total_casts = sum(len(times) for times in player_log.cast_timeline.values())
    uptimes = list(player_log.uptimes.values())
    waste_total = float(sum(player_log.resource_waste.values()))

    return {
        "ctx_encounter_id": float(fight.encounter_id),
        "ctx_difficulty": float(fight.difficulty),
        "ctx_partition": float(fight.partition if fight.partition is not None else -1),
        "nc_item_level": float(build.item_level or 0.0),
        "nc_tier_pieces": float(build.tier_pieces or 0),
        "nc_duration_s": float(duration_s),
        "nc_raid_size": float(raid_size or 0),
        "nc_n_external_buffs": float(len(build.external_buffs)),
        "nc_has_augmentation": 1.0 if build.has_augmentation else 0.0,
        "c_active_time_pct": float(player_log.active_time_pct or 0.0),
        "c_deaths": float(player_log.deaths),
        "c_downtime_s": float(player_log.downtime_s),
        "c_total_casts": float(total_casts),
        "c_casts_per_minute": _safe_div(total_casts, minutes),
        "c_distinct_abilities_cast": float(len(player_log.cast_timeline)),
        "c_mean_uptime": _safe_div(sum(uptimes), len(uptimes)),
        "c_n_tracked_auras": float(len(uptimes)),
        "c_resource_waste_total": waste_total,
        "c_resource_waste_per_minute": _safe_div(waste_total, minutes),
    }


class ExperimentalDatasetBuilder:
    """Read-only over the warehouse; `Store` is the only collaborator."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def build(
        self,
        *,
        partition: int | None = None,
        difficulties: frozenset[int] | None = None,
        observation_keys: frozenset[tuple[str, int, str]] | None = None,
        campaign_id: str | None = None,
    ) -> ExperimentalFeatureDataset:
        rows = self._latest_logs(
            partition=partition, difficulties=difficulties, campaign_id=campaign_id
        )
        if observation_keys is not None:
            rows = [
                row
                for row in rows
                if (str(row["report_code"]), int(str(row["fight_id"])), str(row["player_name"]))
                in observation_keys
            ]
        observations: list[ExperimentalObservation] = []
        skipped = 0
        for row in rows:
            observation = self._to_observation(row)
            if observation is None:
                skipped += 1
                continue
            observations.append(observation)
        return ExperimentalFeatureDataset(
            observations=tuple(observations), skipped_incomplete=skipped
        )

    def _latest_logs(
        self,
        *,
        partition: int | None,
        difficulties: frozenset[int] | None,
        campaign_id: str | None,
    ) -> list[dict[str, object]]:
        """Dedup by the natural key, most recent ingestion wins (§9.1).

        `discovery_fights` supplies raid size and the fight's real start
        time; both are LEFT JOINed because a log may exist without its
        discovery row (e.g. ingested through the interactive path).
        """
        label_expr = "l.percentile"
        campaign_join = ""
        clauses = ["l.kill = true", "l.partition IS NOT NULL"]
        params: dict[str, object] = {}
        if campaign_id is None:
            clauses.append("l.percentile IS NOT NULL")
        else:
            campaign_join = """
            JOIN experiment_campaign_observations eco
              ON eco.report_code=l.report_code AND eco.fight_id=l.fight_id
             AND eco.player_name=l.player_name AND eco.campaign_id=$campaign_id
             AND eco.status='completed'
            """
            label_expr = "eco.rank_percent"
            params["campaign_id"] = campaign_id
        if partition is not None:
            clauses.append("l.partition = $p")
            params["p"] = partition
        where = " AND ".join(clauses)
        frame = self._store.query(
            f"""
            SELECT l.report_code, l.fight_id, l.player_name, l.class_name, l.spec_name,
                   l.encounter_id, l.difficulty, l.partition, {label_expr} AS percentile,
                   l.parquet_path, l.ingested_at,
                   df.size AS raid_size, dr.start_time_ms
            FROM (
                SELECT *, row_number() OVER (
                    PARTITION BY report_code, fight_id, player_name ORDER BY ingested_at DESC
                ) AS rn
                FROM logs
            ) l
            {campaign_join}
            LEFT JOIN discovery_fights df
              ON df.report_code = l.report_code AND df.fight_id = l.fight_id
            LEFT JOIN discovery_reports dr ON dr.report_code = l.report_code
            WHERE l.rn = 1 AND {where}
            ORDER BY l.report_code, l.fight_id, l.player_name
            """,
            **params,
        )
        rows = list(frame.iter_rows(named=True))
        if difficulties is not None:
            rows = [r for r in rows if r["difficulty"] in difficulties]
        return rows

    def _to_observation(self, row: dict[str, object]) -> ExperimentalObservation | None:
        path = row.get("parquet_path")
        if not isinstance(path, str):
            return None
        try:
            player_log = read_parquet_log(Path(path))
        except (OSError, KeyError, ValueError) as exc:
            log.warning("experimental_dataset.unreadable_log", path=path, error=str(exc))
            return None

        rank_percent = float(row["percentile"])  # type: ignore[arg-type]
        if not 0.0 <= rank_percent <= 100.0:
            return None

        raw_size = row.get("raid_size")
        raid_size = int(raw_size) if isinstance(raw_size, int | float) else None
        # Fight start is the split axis. `ingested_at` is when WE fetched it,
        # which says nothing about when the pull happened, so ordering by it
        # would scramble the temporal protocols.
        raw_start = row.get("start_time_ms")
        observed_at_ms = int(raw_start) if isinstance(raw_start, int | float) else 0

        return ExperimentalObservation(
            report_code=str(row["report_code"]),
            fight_id=int(row["fight_id"]),  # type: ignore[arg-type]
            player_name=str(row["player_name"]),
            observed_at_ms=observed_at_ms,
            target=Phase4Target(
                SpecId(str(row["class_name"]), str(row["spec_name"])),
                int(row["encounter_id"]),  # type: ignore[arg-type]
                int(row["difficulty"]),  # type: ignore[arg-type]
                int(row["partition"]),  # type: ignore[arg-type]
            ),
            y_rank_percent=rank_percent,
            features=build_features(player_log, raid_size=raid_size),
        )
