"""A13/A25 without fitting models or touching a historical campaign."""

from dataclasses import replace

import pytest
from test_experiment_evaluate import _wide_dataset

from botgitgud.phase4.experiment_evaluate import EvaluationConfig, dataset_hash, run_matrix
from botgitgud.phase4.experiment_models import FeatureFamily, FittedFeatureSpace
from botgitgud.phase4.experiment_store import FEATURE_SCHEMA_VERSION


def test_a25_value_schema_and_order_are_in_fingerprint() -> None:
    dataset = _wide_dataset(10)
    first = dataset.observations[0]
    changed = replace(first, features={**first.features, "c_deaths": 123456.0})
    assert dataset_hash(dataset) != dataset_hash(
        replace(dataset, observations=(changed, *dataset.observations[1:]))
    )
    reordered = tuple(
        replace(row, features=dict(reversed(list(row.features.items()))))
        for row in reversed(dataset.observations)
    )
    assert dataset_hash(dataset) == dataset_hash(replace(dataset, observations=reordered))
    assert dataset_hash(dataset) != dataset_hash(replace(dataset, feature_schema_version="sae3-v1"))


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_a25_nonfinite_feature_rejected(value: float) -> None:
    dataset = _wide_dataset(10)
    first = replace(dataset.observations[0], features={"c_deaths": value})
    with pytest.raises(ValueError, match="non-finite"):
        dataset_hash(replace(dataset, observations=(first,)))


def test_a25_new_evaluation_rejects_legacy_schema_without_training() -> None:
    dataset = replace(_wide_dataset(10), feature_schema_version="sae3-v1")
    with pytest.raises(ValueError, match="schema"):
        run_matrix(dataset, config=EvaluationConfig(campaign_id="test"), splits=())


def test_a25_result_uses_dataset_version_without_changing_campaign() -> None:
    dataset = _wide_dataset(10)
    old_version = FEATURE_SCHEMA_VERSION
    result = run_matrix(dataset, config=EvaluationConfig(campaign_id="test"), splits=())
    assert result.cells == ()  # No training in this contract test.
    assert (
        result.feature_schema_version
        == dataset.feature_schema_version
        == "experimental-features-v2"
    )
    assert FEATURE_SCHEMA_VERSION == old_version == "sae3-v1"


@pytest.mark.parametrize("family", list(FeatureFamily))
def test_a13_retired_feature_never_reaches_fitted_columns(family: FeatureFamily) -> None:
    dataset = _wide_dataset(10)
    observations = tuple(
        replace(row, features={**row.features, "c_mean_targets_per_cast": float(i)})
        for i, row in enumerate(dataset.observations)
    )
    space = FittedFeatureSpace.fit(observations, family)
    assert space.columns  # prove actual feature selection, not an empty fallback
    assert "c_mean_targets_per_cast" not in space.columns
    without = tuple(
        replace(
            row, features={k: v for k, v in row.features.items() if k != "c_mean_targets_per_cast"}
        )
        for row in observations
    )
    assert space.transform(observations) == space.transform(without)
