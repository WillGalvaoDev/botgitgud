from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from botgitgud.domain.models import FightRef, PlayerBuild, PlayerLog
from botgitgud.errors import PlayerNotFound, RateLimitBudgetExceeded
from botgitgud.ingest.store import Store
from botgitgud.phase4.experiment import ExperimentBudget
from botgitgud.phase4.experiment_campaign import (
    ExperimentCampaign,
    PlannedExperimentObservation,
    StatisticalExperimentPlan,
)
from botgitgud.phase4.experiment_collector import (
    CollectionResult,
    ExperimentCollector,
    FightSession,
)
from botgitgud.phase4.experiment_status import campaign_status
from botgitgud.phase4.experiment_store import (
    CampaignId,
    CollectionStatus,
    ExperimentCampaignStore,
)


def _observation(
    index: int, *, fight_id: int = 1, partition: int = 4, difficulty: int = 5, spec: str = "Fire"
) -> PlannedExperimentObservation:
    return PlannedExperimentObservation(
        report_code="REPORT1234567890",
        fight_id=fight_id,
        player_name=f"Player{index}",
        class_name="Mage",
        spec_name=spec,
        encounter_id=3183,
        difficulty=difficulty,
        partition=partition,
        rank_percent=50.0,
        bucket="40-60",
        start_time_ms=1000 + index,
    )


def _campaign(
    *observations: PlannedExperimentObservation, ceiling: float = 1000
) -> ExperimentCampaign:
    request = StatisticalExperimentPlan(
        partition=observations[0].partition,
        difficulties=frozenset({observations[0].difficulty}),
        budget=ExperimentBudget(max_api_points=ceiling),
        max_observations=len(observations),
    )
    return ExperimentCampaign(
        request,
        tuple(observations),
        len(observations),
        1,
        1,
        17 + max(0, len(observations) - 1) * 2,
        "max_observations",
    )


def _log(observation: PlannedExperimentObservation, *, percentile: float | None = 50) -> PlayerLog:
    return PlayerLog(
        fight=FightRef(
            observation.report_code,
            observation.fight_id,
            observation.encounter_id,
            "Boss",
            observation.difficulty,
            100.0,
            True,
            observation.partition,
        ),
        build=PlayerBuild(
            observation.player_name,
            "Realm",
            observation.class_name,
            observation.spec_name,
            "dps",
            300.0,
            None,
            None,
        ),
        dps=100.0,
        percentile=percentile,
        cast_timeline={},
    )


class FakeBackend:
    def __init__(self, *, fail_player: str | None = None, rate_once: bool = False) -> None:
        self.calls: list[str] = []
        self.payload_loads = 0
        self.fail_player = fail_player
        self.rate_once = rate_once

    def collect(
        self, observation: PlannedExperimentObservation, session: FightSession
    ) -> CollectionResult:
        self.calls.append(observation.player_name)
        if self.rate_once:
            self.rate_once = False
            raise RateLimitBudgetExceeded("pause", points_remaining=0, reset_in_seconds=10)
        if observation.player_name == self.fail_player:
            raise PlayerNotFound("gone")
        hit = session.payload_loaded
        if not hit:
            self.payload_loads += 1
            session.payload_loaded = True
            session.pages_loaded = 3
        return CollectionResult(
            _log(observation), 2 if hit else 17, event_cache_hit=hit, pages_reused=3 if hit else 0
        )


def test_campaign_id_is_deterministic_and_dimension_isolated() -> None:
    campaign = _campaign(_observation(1))
    assert CampaignId.from_campaign(campaign) == CampaignId.from_campaign(campaign)
    different = _campaign(_observation(1, partition=3))
    assert CampaignId.from_campaign(campaign) != CampaignId.from_campaign(different)
    different_difficulty = _campaign(_observation(1, difficulty=4))
    assert CampaignId.from_campaign(campaign) != CampaignId.from_campaign(different_difficulty)


def test_freeze_persists_exact_plan_and_campaigns_do_not_collide(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        campaigns = ExperimentCampaignStore(store)
        first = campaigns.freeze(_campaign(_observation(0), _observation(1)))
        resumed = campaigns.freeze(_campaign(_observation(0), _observation(1)))
        other = campaigns.freeze(_campaign(_observation(2, spec="Frost")))
    assert first.campaign_id == resumed.campaign_id
    assert [item.planned.player_name for item in resumed.observations] == ["Player0", "Player1"]
    assert other.campaign_id != first.campaign_id


def test_shared_fight_reuses_payload_without_changing_selection(tmp_path: Path) -> None:
    backend = FakeBackend()
    with Store(tmp_path) as store:
        campaigns = ExperimentCampaignStore(store)
        frozen = campaigns.freeze(_campaign(_observation(0), _observation(1)))
        summary = ExperimentCollector(campaigns, backend).run(frozen.campaign_id)
        status = campaign_status(campaigns.get(frozen.campaign_id))  # type: ignore[arg-type]
    assert summary.completed == 2
    assert backend.calls == ["Player0", "Player1"]
    assert backend.payload_loads == 1
    assert status.event_cache_hits == 1
    assert status.pages_reused == 3
    assert status.first_player_cost_mean == 17
    assert status.additional_player_cost_mean == 2


def test_completed_failed_and_rejected_are_auditable_and_not_retried(tmp_path: Path) -> None:
    backend = FakeBackend(fail_player="Player1")
    with Store(tmp_path) as store:
        campaigns = ExperimentCampaignStore(store)
        frozen = campaigns.freeze(_campaign(_observation(0), _observation(1)))
        collector = ExperimentCollector(campaigns, backend)
        collector.run(frozen.campaign_id)
        collector.run(frozen.campaign_id)
        current = campaigns.get(frozen.campaign_id)
    assert backend.calls == ["Player0", "Player1"]
    assert [item.status for item in current.observations] == [  # type: ignore[union-attr]
        CollectionStatus.COMPLETED,
        CollectionStatus.FAILED,
    ]


def test_rejection_does_not_select_replacement(tmp_path: Path) -> None:
    class Rejecting(FakeBackend):
        def collect(
            self, observation: PlannedExperimentObservation, session: FightSession
        ) -> CollectionResult:
            return CollectionResult(_log(replace(observation, partition=3)), 17)

    with Store(tmp_path) as store:
        campaigns = ExperimentCampaignStore(store)
        frozen = campaigns.freeze(_campaign(_observation(0)))
        summary = ExperimentCollector(campaigns, Rejecting()).run(frozen.campaign_id)
        current = campaigns.get(frozen.campaign_id)
    assert summary.rejected == 1
    assert len(current.observations) == 1  # type: ignore[union-attr]
    assert current.observations[0].reason == "partition_mismatch"  # type: ignore[union-attr]


def test_campaign_ceiling_stops_before_external_call(tmp_path: Path) -> None:
    backend = FakeBackend()
    with Store(tmp_path) as store:
        campaigns = ExperimentCampaignStore(store)
        frozen = campaigns.freeze(_campaign(_observation(0), ceiling=16))
        summary = ExperimentCollector(campaigns, backend).run(frozen.campaign_id)
    assert summary.stopped_reason == "max_api_points"
    assert summary.pending == 1
    assert backend.calls == []


def test_resume_after_rate_budget_does_not_replan_or_duplicate_points(tmp_path: Path) -> None:
    backend = FakeBackend(rate_once=True)
    with Store(tmp_path) as store:
        campaigns = ExperimentCampaignStore(store)
        frozen = campaigns.freeze(_campaign(_observation(0)))
        collector = ExperimentCollector(campaigns, backend)
        first = collector.run(frozen.campaign_id)
        second = collector.run(frozen.campaign_id)
        third = collector.run(frozen.campaign_id)
    assert first.pending == 1
    assert second.completed == 1
    assert third.api_points_used == second.api_points_used == 17
    assert backend.calls == ["Player0", "Player0"]


def test_interrupted_collecting_is_recovered_as_pending(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        campaigns = ExperimentCampaignStore(store)
        frozen = campaigns.freeze(_campaign(_observation(0), _observation(1)))
        campaigns.mark_collecting(frozen.campaign_id, 0, first=True)
        summary = ExperimentCollector(campaigns, FakeBackend()).run(frozen.campaign_id)
    assert summary.completed == 2
