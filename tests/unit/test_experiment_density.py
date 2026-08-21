from __future__ import annotations

from botgitgud.phase4.experiment_campaign import PlannedExperimentObservation
from botgitgud.phase4.experiment_density import (
    ArchitectureDecision,
    coverage_completed_vs_planned,
    phase4_target_density,
)
from botgitgud.phase4.experiment_store import CollectionStatus, StoredCampaign, StoredObservation

DIFFICULTY = 5
PARTITION = 4


def _planned(ordinal: int, *, spec: str, encounter: int) -> PlannedExperimentObservation:
    return PlannedExperimentObservation(
        report_code=f"R{ordinal:04d}",
        fight_id=ordinal,
        player_name=f"P{ordinal:04d}",
        class_name="Mage",
        spec_name=spec,
        encounter_id=encounter,
        difficulty=DIFFICULTY,
        partition=PARTITION,
        rank_percent=50.0,
        bucket="40-60",
        start_time_ms=1_000 + ordinal,
    )


def _observation(
    ordinal: int, *, status: CollectionStatus, spec: str, encounter: int
) -> StoredObservation:
    return StoredObservation(
        campaign_id="exp-test",
        ordinal=ordinal,
        planned=_planned(ordinal, spec=spec, encounter=encounter),
        status=status,
        attempts=1 if status is not CollectionStatus.PENDING else 0,
        api_points=5.0 if status is CollectionStatus.COMPLETED else 0.0,
        api_points_estimated=True,
        started_at=None,
        completed_at=None,
        reason=None,
        output_reference=None,
        event_cache_hit=False,
        pages_reused=0,
        first_player_in_fight=True,
        reopened_count=0,
    )


def _campaign(observations: list[StoredObservation]) -> StoredCampaign:
    return StoredCampaign(
        campaign_id="exp-test",
        max_api_points=10_000.0,
        estimated_api_points=100.0,
        stopped_reason="budget_exhausted",
        observations=tuple(observations),
    )


# -- density ------------------------------------------------------------------------


def test_density_counts_planned_rows_regardless_of_status() -> None:
    obs = [
        _observation(0, status=CollectionStatus.COMPLETED, spec="A", encounter=1),
        _observation(1, status=CollectionStatus.PENDING, spec="A", encounter=1),
        _observation(2, status=CollectionStatus.COMPLETED, spec="B", encounter=1),
    ]
    stored = _campaign(obs)
    report = phase4_target_density(stored, only_completed=False, thresholds=(2, 3))
    assert report.n_groups == 2  # target A (2 obs) and target B (1 obs)
    assert report.max_obs == 2
    assert report.min_obs == 1
    assert report.n_groups_at_least[2] == 1  # only target A reaches 2
    assert report.n_groups_at_least[3] == 0


def test_density_only_completed_ignores_pending_rows() -> None:
    obs = [
        _observation(0, status=CollectionStatus.COMPLETED, spec="A", encounter=1),
        _observation(1, status=CollectionStatus.PENDING, spec="A", encounter=1),
        _observation(2, status=CollectionStatus.PENDING, spec="A", encounter=1),
    ]
    stored = _campaign(obs)
    planned = phase4_target_density(stored, only_completed=False, thresholds=(2,))
    completed = phase4_target_density(stored, only_completed=True, thresholds=(2,))
    assert planned.max_obs == 3
    assert completed.max_obs == 1
    assert completed.n_groups_at_least[2] == 0


def test_density_does_not_assume_uniform_distribution() -> None:
    """One target gets far more planned rows than another — the report must
    reflect that skew, not average it away.
    """
    obs = [
        _observation(i, status=CollectionStatus.PENDING, spec="Rich", encounter=1) for i in range(9)
    ]
    obs += [_observation(100, status=CollectionStatus.PENDING, spec="Poor", encounter=1)]
    stored = _campaign(obs)
    report = phase4_target_density(stored, only_completed=False, thresholds=(5,))
    assert report.min_obs == 1
    assert report.max_obs == 9
    assert report.n_groups_at_least[5] == 1


def test_density_on_empty_campaign_is_all_zero() -> None:
    stored = _campaign([])
    report = phase4_target_density(stored, thresholds=(10,))
    assert report.n_groups == 0
    assert report.n_groups_at_least[10] == 0


# -- coverage -------------------------------------------------------------------------


def test_coverage_compares_completed_against_full_frozen_plan() -> None:
    obs = [
        _observation(0, status=CollectionStatus.COMPLETED, spec="A", encounter=1),
        _observation(1, status=CollectionStatus.PENDING, spec="B", encounter=2),
    ]
    stored = _campaign(obs)
    coverage = coverage_completed_vs_planned(stored)
    assert coverage.current_specs == 1
    assert coverage.current_encounters == 1
    assert coverage.current_targets == 1
    assert coverage.planned_specs == 2
    assert coverage.planned_encounters == 2
    assert coverage.planned_targets == 2


def test_architecture_decision_vocabulary_has_the_required_labels() -> None:
    labels = {d.value for d in ArchitectureDecision}
    assert labels == {
        "reject_for_current_phase4",
        "keep_as_secondary_candidate",
        "advance_to_validation",
        "insufficient_evidence",
        "not_evaluated",
    }
