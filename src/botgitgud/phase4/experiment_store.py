"""Persistent frozen plans and per-observation checkpoints for experiment collection."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from botgitgud.ingest.store import Store
from botgitgud.phase4.experiment_campaign import ExperimentCampaign, PlannedExperimentObservation

PLANNER_VERSION = "sae2-v1"
FEATURE_SCHEMA_VERSION = "sae3-v1"


class CollectionStatus(StrEnum):
    PENDING = "pending"
    COLLECTING = "collecting"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class CampaignId:
    value: str

    @classmethod
    def from_campaign(cls, campaign: ExperimentCampaign) -> CampaignId:
        request = campaign.request
        payload = {
            "difficulty": sorted(request.difficulties),
            "partition": request.partition,
            "planner_version": PLANNER_VERSION,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "max_observations": request.max_observations,
            "observations": [list(item.observation_key) for item in campaign.observations],
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:20]
        return cls(f"exp-{digest}")


@dataclass(frozen=True, slots=True)
class StoredObservation:
    campaign_id: str
    ordinal: int
    planned: PlannedExperimentObservation
    status: CollectionStatus
    attempts: int
    api_points: float
    api_points_estimated: bool
    started_at: datetime | None
    completed_at: datetime | None
    reason: str | None
    output_reference: str | None
    event_cache_hit: bool
    pages_reused: int
    first_player_in_fight: bool


@dataclass(frozen=True, slots=True)
class StoredCampaign:
    campaign_id: str
    max_api_points: float
    estimated_api_points: float
    stopped_reason: str | None
    observations: tuple[StoredObservation, ...]

    @property
    def consumed_api_points(self) -> float:
        return sum(item.api_points for item in self.observations)

    @property
    def remaining_authorized_points(self) -> float:
        return max(0.0, self.max_api_points - self.consumed_api_points)


_CAMPAIGNS = """
CREATE TABLE IF NOT EXISTS experiment_campaigns (
 campaign_id VARCHAR PRIMARY KEY, planner_version VARCHAR, feature_schema_version VARCHAR,
 difficulty_json VARCHAR, partition INTEGER, max_observations INTEGER,
 max_api_points DOUBLE, estimated_api_points DOUBLE, plan_hash VARCHAR,
 created_at TIMESTAMP, stopped_reason VARCHAR
)"""
_OBSERVATIONS = """
CREATE TABLE IF NOT EXISTS experiment_campaign_observations (
 campaign_id VARCHAR, ordinal INTEGER, report_code VARCHAR, fight_id INTEGER, player_name VARCHAR,
 class_name VARCHAR, spec_name VARCHAR, encounter_id INTEGER, difficulty INTEGER, partition INTEGER,
 rank_percent DOUBLE, bucket VARCHAR, start_time_ms BIGINT, status VARCHAR, attempts INTEGER,
 api_points DOUBLE, started_at TIMESTAMP, completed_at TIMESTAMP, reason VARCHAR,
 output_reference VARCHAR, api_points_estimated BOOLEAN, event_cache_hit BOOLEAN,
 pages_reused INTEGER,
 first_player_in_fight BOOLEAN, PRIMARY KEY (campaign_id, ordinal)
)"""


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ExperimentCampaignStore:
    def __init__(self, store: Store) -> None:
        self._store = store
        store.execute(_CAMPAIGNS)
        store.execute(_OBSERVATIONS)

    def freeze(self, campaign: ExperimentCampaign) -> StoredCampaign:
        campaign_id = CampaignId.from_campaign(campaign).value
        existing = self.get(campaign_id)
        if existing is not None:
            return existing
        request = campaign.request
        self._store.execute(
            "INSERT INTO experiment_campaigns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                campaign_id,
                PLANNER_VERSION,
                FEATURE_SCHEMA_VERSION,
                json.dumps(sorted(request.difficulties)),
                request.partition,
                request.max_observations,
                request.budget.max_api_points,
                campaign.estimated_api_points,
                campaign_id,
                _now(),
                None,
            ],
        )
        for ordinal, item in enumerate(campaign.observations):
            self._store.execute(
                "INSERT INTO experiment_campaign_observations VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, NULL, NULL, NULL, NULL, "
                "true, false, 0, false)",
                [
                    campaign_id,
                    ordinal,
                    item.report_code,
                    item.fight_id,
                    item.player_name,
                    item.class_name,
                    item.spec_name,
                    item.encounter_id,
                    item.difficulty,
                    item.partition,
                    item.rank_percent,
                    item.bucket,
                    item.start_time_ms,
                    CollectionStatus.PENDING.value,
                ],
            )
        frozen = self.get(campaign_id)
        assert frozen is not None
        return frozen

    def get(self, campaign_id: str) -> StoredCampaign | None:
        header = self._store.execute_returning(
            "SELECT max_api_points, estimated_api_points, stopped_reason "
            "FROM experiment_campaigns WHERE campaign_id=?",
            [campaign_id],
        )
        if not header:
            return None
        rows = self._store.execute_returning(
            "SELECT ordinal, report_code, fight_id, player_name, class_name, spec_name, "
            "encounter_id, difficulty, partition, rank_percent, bucket, start_time_ms, status, "
            "attempts, api_points, started_at, completed_at, reason, output_reference, "
            "api_points_estimated, event_cache_hit, pages_reused, first_player_in_fight "
            "FROM experiment_campaign_observations WHERE campaign_id=? ORDER BY ordinal",
            [campaign_id],
        )
        observations = tuple(_stored_observation(campaign_id, row) for row in rows)
        return StoredCampaign(
            campaign_id, float(header[0][0]), float(header[0][1]), header[0][2], observations
        )

    def mark_collecting(self, campaign_id: str, ordinal: int, *, first: bool) -> None:
        self._store.execute(
            "UPDATE experiment_campaign_observations SET status=?, attempts=attempts+1, "
            "started_at=?, first_player_in_fight=? WHERE campaign_id=? AND ordinal=?",
            [CollectionStatus.COLLECTING.value, _now(), first, campaign_id, ordinal],
        )

    def finish(
        self,
        campaign_id: str,
        ordinal: int,
        *,
        status: CollectionStatus,
        points: float = 0,
        reason: str | None = None,
        output: str | None = None,
        cache_hit: bool = False,
        pages_reused: int = 0,
        points_estimated: bool = True,
    ) -> None:
        self._store.execute(
            "UPDATE experiment_campaign_observations SET status=?, api_points=api_points+?, "
            "completed_at=?, reason=?, output_reference=?, api_points_estimated=?, "
            "event_cache_hit=?, pages_reused=? "
            "WHERE campaign_id=? AND ordinal=?",
            [
                status.value,
                points,
                _now(),
                reason,
                output,
                points_estimated,
                cache_hit,
                pages_reused,
                campaign_id,
                ordinal,
            ],
        )

    def reset_collecting(self, campaign_id: str) -> None:
        self._store.execute(
            "UPDATE experiment_campaign_observations SET status=?, reason='interrupted' "
            "WHERE campaign_id=? AND status=?",
            [CollectionStatus.PENDING.value, campaign_id, CollectionStatus.COLLECTING.value],
        )

    def set_stopped_reason(self, campaign_id: str, reason: str) -> None:
        self._store.execute(
            "UPDATE experiment_campaigns SET stopped_reason=? WHERE campaign_id=?",
            [reason, campaign_id],
        )

    def authorize(self, campaign_id: str, total_api_ceiling: float) -> StoredCampaign:
        """Set the cumulative execution ceiling without changing campaign identity."""
        if total_api_ceiling < 0:
            raise ValueError("authorized API ceiling must be non-negative")
        if self.get(campaign_id) is None:
            raise ValueError(f"unknown campaign: {campaign_id}")
        self._store.execute(
            "UPDATE experiment_campaigns SET max_api_points=? WHERE campaign_id=?",
            [total_api_ceiling, campaign_id],
        )
        campaign = self.get(campaign_id)
        assert campaign is not None
        return campaign


def _stored_observation(campaign_id: str, row: tuple[object, ...]) -> StoredObservation:
    planned = PlannedExperimentObservation(
        report_code=str(row[1]),
        fight_id=int(str(row[2])),
        player_name=str(row[3]),
        class_name=str(row[4]),
        spec_name=str(row[5]),
        encounter_id=int(str(row[6])),
        difficulty=int(str(row[7])),
        partition=int(str(row[8])),
        rank_percent=float(str(row[9])),
        bucket=str(row[10]),
        start_time_ms=int(str(row[11])),
    )
    return StoredObservation(
        campaign_id=campaign_id,
        ordinal=int(str(row[0])),
        planned=planned,
        status=CollectionStatus(str(row[12])),
        attempts=int(str(row[13])),
        api_points=float(str(row[14])),
        api_points_estimated=bool(row[19]),
        started_at=row[15] if isinstance(row[15], datetime) else None,
        completed_at=row[16] if isinstance(row[16], datetime) else None,
        reason=row[17] if isinstance(row[17], str) else None,
        output_reference=row[18] if isinstance(row[18], str) else None,
        event_cache_hit=bool(row[20]),
        pages_reused=int(str(row[21])),
        first_player_in_fight=bool(row[22]),
    )
