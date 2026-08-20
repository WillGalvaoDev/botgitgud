"""Safe, resumable executor for a frozen experimental campaign."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from botgitgud.domain.models import PlayerLog
from botgitgud.errors import ApiError, FightNotFound, PlayerNotFound, RateLimitBudgetExceeded
from botgitgud.ingest.log_fetcher import LogFetcher
from botgitgud.phase4.experiment_campaign import PlannedExperimentObservation
from botgitgud.phase4.experiment_store import (
    CollectionStatus,
    ExperimentCampaignStore,
    StoredCampaign,
    StoredObservation,
)


@dataclass(slots=True)
class FightSession:
    fight_key: tuple[str, int]
    payload_loaded: bool = False
    pages_loaded: int = 0


@dataclass(frozen=True, slots=True)
class CollectionResult:
    player_log: PlayerLog
    api_points: float
    api_points_estimated: bool = True
    event_cache_hit: bool = False
    pages_reused: int = 0


class ObservationBackend(Protocol):
    def collect(
        self, observation: PlannedExperimentObservation, session: FightSession
    ) -> CollectionResult: ...


class LogFetcherBackend:
    """Production adapter. Grouping/session is explicit and accounting is measured
    from LogFetcher's query counter. Existing player-specific WCL queries are not
    claimed as shared; cache hits are reported only when Store avoids all queries.
    """

    def __init__(self, fetcher: LogFetcher) -> None:
        self._fetcher = fetcher

    def collect(
        self, observation: PlannedExperimentObservation, session: FightSession
    ) -> CollectionResult:
        before = self._fetcher.query_count
        player_log = self._fetcher.fetch(
            observation.report_code, observation.fight_id, observation.player_name
        )
        queries = self._fetcher.query_count - before
        cache_hit = queries == 0
        session.payload_loaded = True
        return CollectionResult(player_log, queries * 2.0, True, cache_hit, 0)


@dataclass(frozen=True, slots=True)
class CollectionSummary:
    campaign_id: str
    planned: int
    completed: int
    failed: int
    rejected: int
    pending: int
    api_points_used: float
    stopped_reason: str


class ExperimentCollector:
    def __init__(self, campaigns: ExperimentCampaignStore, backend: ObservationBackend) -> None:
        self._campaigns = campaigns
        self._backend = backend

    def run(
        self, campaign_id: str, *, authorized_api_ceiling: float | None = None
    ) -> CollectionSummary:
        if authorized_api_ceiling is not None:
            self._campaigns.authorize(campaign_id, authorized_api_ceiling)
        self._campaigns.reset_collecting(campaign_id)
        campaign = self._require_campaign(campaign_id)
        sessions: dict[tuple[str, int], FightSession] = {}
        execution = _execution_order(campaign.observations)
        stopped = "completed"
        for stored in execution:
            if stored.status is not CollectionStatus.PENDING:
                continue
            used = sum(item.api_points for item in self._require_campaign(campaign_id).observations)
            session = sessions.setdefault(
                stored.planned.fight_key, FightSession(stored.planned.fight_key)
            )
            estimated_delta = 2.0 if session.payload_loaded else 17.0
            if used + estimated_delta > campaign.max_api_points:
                stopped = "budget_exhausted"
                break
            first = not session.payload_loaded
            self._campaigns.mark_collecting(campaign_id, stored.ordinal, first=first)
            try:
                result = self._backend.collect(stored.planned, session)
            except RateLimitBudgetExceeded:
                self._campaigns.reset_collecting(campaign_id)
                stopped = "rate_limit_budget"
                break
            except PlayerNotFound as exc:
                self._fail(stored, "player_unavailable", exc)
                continue
            except FightNotFound as exc:
                self._fail(stored, "fight_unavailable", exc)
                continue
            except ApiError as exc:
                self._fail(stored, "api_failure", exc)
                continue
            rejection = _validate(stored.planned, result.player_log)
            if rejection is not None:
                self._campaigns.finish(
                    campaign_id,
                    stored.ordinal,
                    status=CollectionStatus.REJECTED,
                    points=result.api_points,
                    reason=rejection,
                    cache_hit=result.event_cache_hit,
                    pages_reused=result.pages_reused,
                    points_estimated=result.api_points_estimated,
                )
                continue
            reference = (
                f"logs://{stored.planned.report_code}/{stored.planned.fight_id}/"
                f"{stored.planned.player_name}"
            )
            self._campaigns.finish(
                campaign_id,
                stored.ordinal,
                status=CollectionStatus.COMPLETED,
                points=result.api_points,
                output=reference,
                cache_hit=result.event_cache_hit,
                pages_reused=result.pages_reused,
                points_estimated=result.api_points_estimated,
            )
        self._campaigns.set_stopped_reason(campaign_id, stopped)
        return _summary(self._require_campaign(campaign_id), stopped)

    def _fail(self, stored: StoredObservation, reason: str, exc: Exception) -> None:
        self._campaigns.finish(
            stored.campaign_id,
            stored.ordinal,
            status=CollectionStatus.FAILED,
            reason=f"{reason}: {exc}",
        )

    def _require_campaign(self, campaign_id: str) -> StoredCampaign:
        campaign = self._campaigns.get(campaign_id)
        if campaign is None:
            raise ValueError(f"unknown campaign: {campaign_id}")
        return campaign


def _execution_order(observations: tuple[StoredObservation, ...]) -> list[StoredObservation]:
    """Stable fight locality: first occurrence orders fights; ordinal orders players.
    The frozen selection is never changed.
    """
    fight_order: dict[tuple[str, int], int] = {}
    for item in observations:
        fight_order.setdefault(item.planned.fight_key, len(fight_order))
    return sorted(
        observations, key=lambda item: (fight_order[item.planned.fight_key], item.ordinal)
    )


def _validate(planned: PlannedExperimentObservation, player_log: PlayerLog) -> str | None:
    fight, build = player_log.fight, player_log.build
    if fight.partition != planned.partition:
        return "partition_mismatch"
    if fight.difficulty != planned.difficulty:
        return "difficulty_mismatch"
    if fight.encounter_id != planned.encounter_id:
        return "encounter_mismatch"
    if (build.class_name, build.spec_name) != (planned.class_name, planned.spec_name):
        return "spec_mismatch"
    if player_log.percentile is None:
        return "percentile_missing"
    return None


def _summary(campaign: StoredCampaign, stopped: str) -> CollectionSummary:
    counts = {status: 0 for status in CollectionStatus}
    for item in campaign.observations:
        counts[item.status] += 1
    return CollectionSummary(
        campaign.campaign_id,
        len(campaign.observations),
        counts[CollectionStatus.COMPLETED],
        counts[CollectionStatus.FAILED],
        counts[CollectionStatus.REJECTED],
        counts[CollectionStatus.PENDING],
        sum(item.api_points for item in campaign.observations),
        stopped,
    )
