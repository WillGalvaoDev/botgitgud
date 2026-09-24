from __future__ import annotations

import pytest

from botgitgud.domain.specs import SpecId
from botgitgud.phase4.experiment import (
    IMPLEMENTED_GRANULARITIES,
    MODELLABLE_ROLES,
    PERCENTILE_BUCKETS,
    ExperimentBudget,
    FeatureRole,
    ModelGranularity,
    SplitProtocol,
    grouping_key,
    percentile_bucket,
)
from botgitgud.phase4.target import Phase4Target

TARGET = Phase4Target(SpecId("DeathKnight", "Unholy"), 3183, 5, 4)


# -- percentile buckets --------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, "00-20"),
        (19.999, "00-20"),
        (20.0, "20-40"),
        (39.9, "20-40"),
        (40.0, "40-60"),
        (59.9, "40-60"),
        (60.0, "60-80"),
        (79.9, "60-80"),
        (80.0, "80-100"),
        (100.0, "80-100"),
    ],
)
def test_percentile_bucket_boundaries(value: float, expected: str) -> None:
    assert percentile_bucket(value) == expected


@pytest.mark.parametrize("value", [-0.1, 100.1, -5.0, 250.0])
def test_percentile_bucket_rejects_out_of_range(value: float) -> None:
    with pytest.raises(ValueError, match="rank_percent"):
        percentile_bucket(value)


def test_every_bucket_is_reachable() -> None:
    produced = {percentile_bucket(v) for v in (0, 20, 40, 60, 80, 100)}
    assert produced == set(PERCENTILE_BUCKETS)


# -- model granularity ---------------------------------------------------------


def test_grouping_key_per_granularity() -> None:
    assert grouping_key(ModelGranularity.MODEL_TARGET, TARGET) == "DeathKnight/Unholy/3183/5/4"
    assert grouping_key(ModelGranularity.MODEL_SPEC, TARGET) == "DeathKnight/Unholy"
    assert grouping_key(ModelGranularity.MODEL_ENCOUNTER, TARGET) == "3183/5/4"
    assert grouping_key(ModelGranularity.MODEL_GLOBAL, TARGET) == "global"


def test_model_target_grouping_key_equals_target_id() -> None:
    assert grouping_key(ModelGranularity.MODEL_TARGET, TARGET) == TARGET.target_id


def test_hierarchical_granularity_is_an_extension_point_not_an_implementation() -> None:
    """Doc §5: E must never silently behave like global."""
    with pytest.raises(NotImplementedError, match="extension point"):
        grouping_key(ModelGranularity.MODEL_HIERARCHICAL, TARGET)


def test_hierarchical_is_excluded_from_implemented_granularities() -> None:
    expected = {
        ModelGranularity.MODEL_TARGET,
        ModelGranularity.MODEL_SPEC,
        ModelGranularity.MODEL_ENCOUNTER,
        ModelGranularity.MODEL_GLOBAL,
    }
    assert ModelGranularity.MODEL_HIERARCHICAL not in IMPLEMENTED_GRANULARITIES
    assert set(IMPLEMENTED_GRANULARITIES) == expected


def test_different_targets_share_a_spec_key_but_not_a_target_key() -> None:
    other = Phase4Target(SpecId("DeathKnight", "Unholy"), 3182, 5, 4)
    assert grouping_key(ModelGranularity.MODEL_SPEC, other) == grouping_key(
        ModelGranularity.MODEL_SPEC, TARGET
    )
    assert grouping_key(ModelGranularity.MODEL_TARGET, other) != grouping_key(
        ModelGranularity.MODEL_TARGET, TARGET
    )


def test_different_specs_share_an_encounter_key() -> None:
    other = Phase4Target(SpecId("Mage", "Frost"), 3183, 5, 4)
    assert grouping_key(ModelGranularity.MODEL_ENCOUNTER, other) == grouping_key(
        ModelGranularity.MODEL_ENCOUNTER, TARGET
    )


# -- split protocols -----------------------------------------------------------


def test_all_five_split_protocols_exist() -> None:
    assert len(list(SplitProtocol)) == 5


def test_no_plain_random_split_protocol_exists() -> None:
    """Doc §8: a simple random split is never the primary validation."""
    assert not any("random" in p.value for p in SplitProtocol)


# -- feature roles -------------------------------------------------------------


def test_leakage_and_target_and_identity_are_never_modellable() -> None:
    assert FeatureRole.EXCLUDED_LEAKAGE not in MODELLABLE_ROLES
    assert FeatureRole.TARGET not in MODELLABLE_ROLES
    assert FeatureRole.IDENTITY not in MODELLABLE_ROLES


def test_modellable_roles_are_exactly_the_three_feature_roles() -> None:
    expected = {
        FeatureRole.CONTEXT,
        FeatureRole.CONTROLLABLE,
        FeatureRole.NON_CONTROLLABLE,
    }
    assert set(MODELLABLE_ROLES) == expected


# -- budget --------------------------------------------------------------------


def test_default_budget_matches_the_approved_ceiling() -> None:
    assert ExperimentBudget().max_api_points == 25_000.0


def test_solo_observation_costs_seventeen_points() -> None:
    """Reproduces ingest/backfill_planner.py's measured 17 pts/observation
    when no fight is shared (docs/phase4.md).
    """
    budget = ExperimentBudget()
    assert budget.estimate_points(n_fights=1, n_observations=1) == 17.0


def test_sharing_a_fight_is_cheaper_than_two_solo_fights() -> None:
    budget = ExperimentBudget()
    shared = budget.estimate_points(n_fights=1, n_observations=2)
    solo = budget.estimate_points(n_fights=2, n_observations=2)
    assert shared == 19.0
    assert solo == 34.0
    assert shared < solo


def test_max_observations_solo_under_the_default_ceiling() -> None:
    assert ExperimentBudget().max_observations_solo() == 1470


def test_budget_rejects_non_positive_ceiling() -> None:
    with pytest.raises(ValueError, match="max_api_points"):
        ExperimentBudget(max_api_points=0.0)


def test_budget_rejects_negative_costs() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        ExperimentBudget(points_per_fight_shared=-1.0)


def test_estimate_rejects_negative_counts() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        ExperimentBudget().estimate_points(n_fights=-1, n_observations=1)


def test_estimate_is_zero_for_an_empty_campaign() -> None:
    assert ExperimentBudget().estimate_points(n_fights=0, n_observations=0) == 0.0
