"""Campaign-level collection status and honest API cost instrumentation."""

from __future__ import annotations

from dataclasses import dataclass

from botgitgud.phase4.experiment_store import CollectionStatus, StoredCampaign


@dataclass(frozen=True, slots=True)
class CampaignStatus:
    campaign_id: str
    planned: int
    pending: int
    collecting: int
    completed: int
    failed: int
    rejected: int
    api_points_used: float
    api_point_ceiling: float
    api_points_estimated: bool
    unique_fights_completed: int
    observations_per_fight: float | None
    event_cache_hits: int
    event_cache_hit_rate: float | None
    pages_reused: int
    points_per_completed_observation: float | None
    points_per_unique_fight: float | None
    first_player_cost_mean: float | None
    additional_player_cost_mean: float | None
    planned_cost: float
    planned_vs_actual_ratio: float | None
    specs_represented: int
    encounters_represented: int
    targets_represented: int
    temporal_span_ms: tuple[int, int] | None
    stopped_reason: str | None


def campaign_status(campaign: StoredCampaign) -> CampaignStatus:
    counts = {status: 0 for status in CollectionStatus}
    for item in campaign.observations:
        counts[item.status] += 1
    completed = [
        item for item in campaign.observations if item.status is CollectionStatus.COMPLETED
    ]
    fights = {item.planned.fight_key for item in completed}
    points = sum(item.api_points for item in campaign.observations)
    first_costs = [item.api_points for item in completed if item.first_player_in_fight]
    additional_costs = [item.api_points for item in completed if not item.first_player_in_fight]
    cache_hits = sum(item.event_cache_hit for item in completed)
    times = [item.planned.start_time_ms for item in completed]
    return CampaignStatus(
        campaign_id=campaign.campaign_id,
        planned=len(campaign.observations),
        pending=counts[CollectionStatus.PENDING],
        collecting=counts[CollectionStatus.COLLECTING],
        completed=len(completed),
        failed=counts[CollectionStatus.FAILED],
        rejected=counts[CollectionStatus.REJECTED],
        api_points_used=points,
        api_point_ceiling=campaign.max_api_points,
        api_points_estimated=any(
            item.api_points_estimated for item in campaign.observations if item.api_points
        ),
        unique_fights_completed=len(fights),
        observations_per_fight=len(completed) / len(fights) if fights else None,
        event_cache_hits=cache_hits,
        event_cache_hit_rate=cache_hits / len(completed) if completed else None,
        pages_reused=sum(item.pages_reused for item in completed),
        points_per_completed_observation=points / len(completed) if completed else None,
        points_per_unique_fight=points / len(fights) if fights else None,
        first_player_cost_mean=_mean(first_costs),
        additional_player_cost_mean=_mean(additional_costs),
        planned_cost=campaign.estimated_api_points,
        planned_vs_actual_ratio=(
            points / campaign.estimated_api_points if campaign.estimated_api_points else None
        ),
        specs_represented=len({item.planned.spec_key for item in completed}),
        encounters_represented=len({item.planned.encounter_id for item in completed}),
        targets_represented=len({item.planned.target.target_id for item in completed}),
        temporal_span_ms=(min(times), max(times)) if times else None,
        stopped_reason=campaign.stopped_reason,
    )


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None
