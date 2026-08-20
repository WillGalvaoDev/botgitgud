"""Safe, resumable executor for a frozen experimental campaign."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from botgitgud.domain.models import PlayerLog
from botgitgud.errors import (
    ApiError,
    FightNotFound,
    PlayerNotFound,
    RateLimitBudgetExceeded,
    RateLimitCheckFailed,
)
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
    query_cache: dict[tuple[str, str], dict[str, Any]] | None = None


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
        if session.query_cache is None:
            session.query_cache = {}
        cache_entries_before = len(session.query_cache) if session.payload_loaded else 0
        player_log = self._fetcher.fetch(
            observation.report_code,
            observation.fight_id,
            observation.player_name,
            experimental_label=observation.rank_percent,
            fight_query_cache=session.query_cache,
        )
        queries = self._fetcher.query_count - before
        reused = cache_entries_before
        cache_hit = queries == 0 or reused > 0
        session.payload_loaded = True
        return CollectionResult(player_log, queries * 2.0, True, cache_hit, reused)


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
        # A stale stopped_reason from a previous run must never be mistaken
        # for this run's outcome — if this run itself crashes via some
        # future, still-undiscovered uncaught path, the persisted value
        # will honestly read "in_progress" rather than an old terminal
        # reason. Every exit below overwrites this before returning.
        self._campaigns.set_stopped_reason(campaign_id, "in_progress")
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
            except RateLimitCheckFailed:
                # The rate-limit check itself failed after retries (remote
                # state unknown) — fail closed exactly like a known-low
                # budget: never assume points exist. Distinct reason so
                # this is never confused with an actually-measured floor.
                self._campaigns.reset_collecting(campaign_id)
                stopped = "rate_limit_refresh_failed"
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
    if fight.report_code != planned.report_code:
        return "report_mismatch"
    if fight.fight_id != planned.fight_id:
        return "fight_mismatch"
    if build.character_name != planned.player_name:
        return "player_mismatch"
    if fight.partition != planned.partition:
        return "partition_mismatch"
    if fight.difficulty != planned.difficulty:
        return "difficulty_mismatch"
    if fight.encounter_id != planned.encounter_id:
        return "encounter_mismatch"
    if (build.class_name, build.spec_name) != (planned.class_name, planned.spec_name):
        return "spec_mismatch"
    if not 0.0 <= planned.rank_percent <= 100.0:
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
