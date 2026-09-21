from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from botgitgud.domain.models import FightRef, PlayerBuild, PlayerLog
from botgitgud.errors import PlayerNotFound, RateLimitBudgetExceeded, RateLimitCheckFailed
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
    LogFetcherBackend,
    _validate,
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
    *observations: PlannedExperimentObservation,
    ceiling: float | None = 1000,
    max_observations: int | None = None,
) -> ExperimentCampaign:
    request = StatisticalExperimentPlan(
        partition=observations[0].partition,
        difficulties=frozenset({observations[0].difficulty}),
        budget=ExperimentBudget() if ceiling is None else ExperimentBudget(max_api_points=ceiling),
        max_observations=max_observations or len(observations),
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
    def __init__(
        self,
        *,
        fail_player: str | None = None,
        rate_once: bool = False,
        refresh_fails_once: bool = False,
    ) -> None:
        self.calls: list[str] = []
        self.payload_loads = 0
        self.fail_player = fail_player
        self.rate_once = rate_once
        self.refresh_fails_once = refresh_fails_once

    def collect(
        self, observation: PlannedExperimentObservation, session: FightSession
    ) -> CollectionResult:
        self.calls.append(observation.player_name)
        if self.rate_once:
            self.rate_once = False
            raise RateLimitBudgetExceeded("pause", points_remaining=0, reset_in_seconds=10)
        if self.refresh_fails_once:
            # Fails exactly between "observation -> collecting" and any real
            # network I/O for this observation — mirroring the real incident,
            # where the crash happened before any bytes for this attempt were
            # exchanged (mark_collecting already ran; collect() raises first).
            self.refresh_fails_once = False
            raise RateLimitCheckFailed("refresh unavailable")
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


def test_campaign_id_ignores_execution_budget_but_not_scientific_plan() -> None:
    observation = _observation(1)
    identity = CampaignId.from_campaign(_campaign(observation, ceiling=8040))
    assert identity == CampaignId.from_campaign(_campaign(observation, ceiling=9000))
    assert identity == CampaignId.from_campaign(_campaign(observation, ceiling=None))
    assert identity != CampaignId.from_campaign(
        _campaign(observation, ceiling=8040, max_observations=2)
    )
    assert identity != CampaignId.from_campaign(_campaign(_observation(2), ceiling=8040))


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
    assert summary.stopped_reason == "budget_exhausted"
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


def test_larger_cumulative_ceiling_preserves_history_and_limits_increment(tmp_path: Path) -> None:
    observations = tuple(_observation(i, fight_id=i + 1) for i in range(58))
    backend = FakeBackend()
    with Store(tmp_path) as store:
        campaigns = ExperimentCampaignStore(store)
        frozen = campaigns.freeze(_campaign(*observations, ceiling=8040))
        campaigns.finish(frozen.campaign_id, 0, status=CollectionStatus.COMPLETED, points=8040)
        summary = ExperimentCollector(campaigns, backend).run(
            frozen.campaign_id, authorized_api_ceiling=9000
        )
        current = campaigns.get(frozen.campaign_id)
        status = campaign_status(current)  # type: ignore[arg-type]
    assert summary.stopped_reason == "budget_exhausted"
    assert summary.api_points_used == 8992
    assert len(backend.calls) == 56
    assert current.observations[0].status is CollectionStatus.COMPLETED  # type: ignore[union-attr]
    assert status.authorized_api_ceiling == 9000
    assert status.consumed_api_points == 8992
    assert status.remaining_authorized_points == 8


def test_ceiling_below_historical_consumption_makes_zero_calls(tmp_path: Path) -> None:
    backend = FakeBackend()
    with Store(tmp_path) as store:
        campaigns = ExperimentCampaignStore(store)
        frozen = campaigns.freeze(_campaign(_observation(0), _observation(1)))
        campaigns.finish(frozen.campaign_id, 0, status=CollectionStatus.FAILED, points=5000)
        summary = ExperimentCollector(campaigns, backend).run(
            frozen.campaign_id, authorized_api_ceiling=4000
        )
        current = campaigns.get(frozen.campaign_id)
    assert summary.stopped_reason == "budget_exhausted"
    assert summary.api_points_used == 5000
    assert backend.calls == []
    assert current.observations[0].status is CollectionStatus.FAILED  # type: ignore[union-attr]
    assert len(current.observations) == 2  # type: ignore[union-attr]


def test_larger_authorization_preserves_failed_and_rejected(tmp_path: Path) -> None:
    backend = FakeBackend()
    with Store(tmp_path) as store:
        campaigns = ExperimentCampaignStore(store)
        frozen = campaigns.freeze(_campaign(_observation(0), _observation(1), ceiling=20))
        campaigns.finish(frozen.campaign_id, 0, status=CollectionStatus.FAILED, reason="gone")
        campaigns.finish(
            frozen.campaign_id,
            1,
            status=CollectionStatus.REJECTED,
            reason="partition_mismatch",
        )
        ExperimentCollector(campaigns, backend).run(frozen.campaign_id, authorized_api_ceiling=9000)
        current = campaigns.get(frozen.campaign_id)
    assert backend.calls == []
    assert [item.status for item in current.observations] == [  # type: ignore[union-attr]
        CollectionStatus.FAILED,
        CollectionStatus.REJECTED,
    ]


def test_frozen_label_is_authoritative_and_extremes_are_preserved() -> None:
    class CapturingFetcher:
        query_count = 0

        def fetch(self, report: str, fight: int, player: str, **kwargs: object) -> PlayerLog:
            label = kwargs["experimental_label"]
            assert isinstance(label, float) and label in {6.0, 100.0}
            observation = _observation(0)
            return _log(observation, percentile=label)

    backend = LogFetcherBackend(CapturingFetcher())  # type: ignore[arg-type]
    for label in (6.0, 100.0):
        observation = replace(_observation(0), rank_percent=label)
        result = backend.collect(observation, FightSession(observation.fight_key))
        assert result.player_log.percentile == label


def test_complete_identity_is_validated_before_frozen_label_is_accepted() -> None:
    planned = _observation(0)
    assert _validate(planned, _log(planned)) is None
    cases = [
        (replace(planned, report_code="OTHER"), "report_mismatch"),
        (replace(planned, fight_id=2), "fight_mismatch"),
        (replace(planned, player_name="Other"), "player_mismatch"),
        (replace(planned, spec_name="Frost"), "spec_mismatch"),
        (replace(planned, encounter_id=999), "encounter_mismatch"),
        (replace(planned, difficulty=4), "difficulty_mismatch"),
        (replace(planned, partition=3), "partition_mismatch"),
    ]
    for payload_identity, reason in cases:
        assert _validate(planned, _log(payload_identity)) == reason
    assert _validate(replace(planned, rank_percent=float("nan")), _log(planned)) == (
        "percentile_missing"
    )


def test_reopen_known_incident_preserves_plan_attempt_and_accounting(tmp_path: Path) -> None:
    first, second, third = _observation(0), _observation(1), _observation(2)
    with Store(tmp_path) as store:
        campaigns = ExperimentCampaignStore(store)
        frozen = campaigns.freeze(_campaign(first, second, third))
        original_keys = tuple(item.planned.observation_key for item in frozen.observations)
        for ordinal, reason in [(0, "percentile_missing"), (1, "partition_mismatch")]:
            campaigns.mark_collecting(frozen.campaign_id, ordinal, first=ordinal == 0)
            campaigns.finish(
                frozen.campaign_id,
                ordinal,
                status=CollectionStatus.REJECTED,
                points=28,
                reason=reason,
            )
        campaigns.mark_collecting(frozen.campaign_id, 2, first=False)
        campaigns.finish(frozen.campaign_id, 2, status=CollectionStatus.COMPLETED, points=2)
        reopened = campaigns.reopen_percentile_integration_rejections(frozen.campaign_id)
        current = campaigns.get(frozen.campaign_id)
        history = campaigns.attempts(frozen.campaign_id, 0)
    assert reopened == 1
    assert current is not None and current.campaign_id == frozen.campaign_id
    assert tuple(item.planned.observation_key for item in current.observations) == original_keys
    assert [item.status for item in current.observations] == [
        CollectionStatus.PENDING,
        CollectionStatus.REJECTED,
        CollectionStatus.COMPLETED,
    ]
    assert current.consumed_api_points == 58
    assert current.observations[0].attempts == 1
    assert current.observations[0].reopened_count == 1
    assert [(item.status, item.reason, item.api_points) for item in history] == [
        (CollectionStatus.REJECTED, "percentile_missing", 28)
    ]


# -- rate-limit refresh failure: fault injection at the collector boundary ----
# Reproduces the real incident (docs/fase4-experiment-collection.md): a
# transient network failure during WclClient's periodic budget refresh
# crashed the collector process with an unhandled exception, leaving one
# observation stuck `collecting` and the campaign's stopped_reason stale.


def test_rate_limit_refresh_failure_stops_cleanly_with_a_distinct_reason(tmp_path: Path) -> None:
    backend = FakeBackend(refresh_fails_once=True)
    with Store(tmp_path) as store:
        campaigns = ExperimentCampaignStore(store)
        frozen = campaigns.freeze(_campaign(_observation(0), _observation(1)))
        summary = ExperimentCollector(campaigns, backend).run(frozen.campaign_id)
    assert summary.stopped_reason == "rate_limit_refresh_failed"
    assert summary.stopped_reason not in {"budget_exhausted", "rate_limit_budget"}


def test_rate_limit_refresh_failure_makes_zero_further_collection_calls(tmp_path: Path) -> None:
    backend = FakeBackend(refresh_fails_once=True)
    with Store(tmp_path) as store:
        campaigns = ExperimentCampaignStore(store)
        frozen = campaigns.freeze(_campaign(_observation(0), _observation(1), _observation(2)))
        ExperimentCollector(campaigns, backend).run(frozen.campaign_id)
    assert backend.calls == ["Player0"]  # the failing attempt only — no further players tried


def test_stale_stopped_reason_never_survives_a_new_run(tmp_path: Path) -> None:
    backend = FakeBackend(refresh_fails_once=True)
    with Store(tmp_path) as store:
        campaigns = ExperimentCampaignStore(store)
        frozen = campaigns.freeze(_campaign(_observation(0)))
        campaigns.set_stopped_reason(frozen.campaign_id, "rate_limit_budget")  # stale, prior run
        ExperimentCollector(campaigns, backend).run(frozen.campaign_id)
        current = campaigns.get(frozen.campaign_id)
        assert current is not None
    assert current.stopped_reason == "rate_limit_refresh_failed"  # not the old value


def test_rate_limit_refresh_failure_recovers_collecting_to_pending_immediately(
    tmp_path: Path,
) -> None:
    backend = FakeBackend(refresh_fails_once=True)
    with Store(tmp_path) as store:
        campaigns = ExperimentCampaignStore(store)
        frozen = campaigns.freeze(_campaign(_observation(0)))
        ExperimentCollector(campaigns, backend).run(frozen.campaign_id)
        current = campaigns.get(frozen.campaign_id)
        assert current is not None
    assert current.observations[0].status is CollectionStatus.PENDING
    assert current.observations[0].reason == "interrupted"


def test_rate_limit_refresh_failure_preserves_prior_terminal_observations(tmp_path: Path) -> None:
    backend = FakeBackend(refresh_fails_once=True)
    with Store(tmp_path) as store:
        campaigns = ExperimentCampaignStore(store)
        frozen = campaigns.freeze(
            _campaign(_observation(0), _observation(1), _observation(2), ceiling=1000)
        )
        campaigns.finish(frozen.campaign_id, 0, status=CollectionStatus.COMPLETED, points=17)
        campaigns.finish(frozen.campaign_id, 1, status=CollectionStatus.FAILED, reason="gone")
        ExperimentCollector(campaigns, backend).run(frozen.campaign_id)
        current = campaigns.get(frozen.campaign_id)
        assert current is not None
    assert current.observations[0].status is CollectionStatus.COMPLETED
    assert current.observations[1].status is CollectionStatus.FAILED
    assert current.observations[2].status is CollectionStatus.PENDING  # the one that hit the fault


def test_rate_limit_refresh_failure_does_not_fabricate_accounting(tmp_path: Path) -> None:
    backend = FakeBackend(refresh_fails_once=True)
    with Store(tmp_path) as store:
        campaigns = ExperimentCampaignStore(store)
        frozen = campaigns.freeze(_campaign(_observation(0), _observation(1), ceiling=1000))
        campaigns.finish(frozen.campaign_id, 0, status=CollectionStatus.COMPLETED, points=17)
        summary = ExperimentCollector(campaigns, backend).run(frozen.campaign_id)
        current = campaigns.get(frozen.campaign_id)
        assert current is not None
    # Historical points untouched; nothing invented for the incomplete attempt.
    assert current.consumed_api_points == 17
    assert summary.api_points_used == 17
    assert current.observations[1].api_points == 0
    assert current.observations[1].attempts == 1  # exactly the one real attempt, not doubled


def test_rate_limit_refresh_failure_does_not_change_the_authorized_ceiling(tmp_path: Path) -> None:
    backend = FakeBackend(refresh_fails_once=True)
    with Store(tmp_path) as store:
        campaigns = ExperimentCampaignStore(store)
        frozen = campaigns.freeze(_campaign(_observation(0), ceiling=500))
        ExperimentCollector(campaigns, backend).run(frozen.campaign_id)  # no ceiling kwarg passed
        current = campaigns.get(frozen.campaign_id)
        assert current is not None
    assert current.max_api_points == 500  # unchanged — a retry never grants extra budget


def test_recovery_after_refresh_failure_does_not_replan_or_change_identity(tmp_path: Path) -> None:
    backend = FakeBackend(refresh_fails_once=True)
    with Store(tmp_path) as store:
        campaigns = ExperimentCampaignStore(store)
        frozen = campaigns.freeze(_campaign(_observation(0), _observation(1)))
        original_id = frozen.campaign_id
        original_keys = tuple(item.planned.observation_key for item in frozen.observations)

        collector = ExperimentCollector(campaigns, backend)
        first_summary = collector.run(original_id)  # hits the fault, recovers ordinal 0

        clean_backend = FakeBackend()
        second_summary = ExperimentCollector(campaigns, clean_backend).run(original_id)
        current = campaigns.get(original_id)
        assert current is not None

    assert first_summary.campaign_id == original_id
    assert second_summary.campaign_id == original_id
    assert current.campaign_id == original_id  # no replacement campaign was created
    assert len(current.observations) == 2  # frozen plan size unchanged — no replanning
    assert (
        tuple(item.planned.observation_key for item in current.observations) == original_keys
    )  # same ordinals, same report/fight/player identity — byte-equivalent plan
    assert second_summary.completed == 2  # second run continues cleanly via the fake backend
    assert second_summary.stopped_reason == "completed"


def test_rate_limit_refresh_failure_never_fabricates_a_replacement_observation(
    tmp_path: Path,
) -> None:
    backend = FakeBackend(refresh_fails_once=True)
    with Store(tmp_path) as store:
        campaigns = ExperimentCampaignStore(store)
        frozen = campaigns.freeze(_campaign(_observation(0)))
        target = frozen.observations[0].planned
        ExperimentCollector(campaigns, backend).run(frozen.campaign_id)
        current = campaigns.get(frozen.campaign_id)
        assert current is not None
    recovered = current.observations[0].planned
    assert recovered.report_code == target.report_code
    assert recovered.fight_id == target.fight_id
    assert recovered.player_name == target.player_name
    assert recovered.rank_percent == target.rank_percent
