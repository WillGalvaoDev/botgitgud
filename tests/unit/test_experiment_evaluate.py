from __future__ import annotations

from botgitgud.domain.specs import SpecId
from botgitgud.phase4.experiment import ModelGranularity, SplitProtocol
from botgitgud.phase4.experiment_evaluate import (
    ALL_GRANULARITIES,
    CellStatus,
    EvaluationConfig,
    GroupStatus,
    ModelKind,
    dataset_hash,
    evaluate_cell,
    run_matrix,
)
from botgitgud.phase4.experiment_models import FeatureFamily
from botgitgud.phase4.experiment_splits import temporal_within_target_split
from botgitgud.phase4.experimental_dataset import (
    ExperimentalFeatureDataset,
    ExperimentalObservation,
)
from botgitgud.phase4.target import Phase4Target

DIFFICULTY = 5
PARTITION = 4


def _obs(
    index: int,
    *,
    class_name: str = "Mage",
    spec_name: str = "Frost",
    encounter_id: int = 3176,
    at_ms: int | None = None,
) -> ExperimentalObservation:
    return ExperimentalObservation(
        report_code=f"R{index:04d}",
        fight_id=index,
        player_name=f"P{index:04d}",
        observed_at_ms=at_ms if at_ms is not None else 1_000 + index,
        target=Phase4Target(SpecId(class_name, spec_name), encounter_id, DIFFICULTY, PARTITION),
        y_rank_percent=float((index * 7) % 101),
        features={
            "ctx_encounter_id": float(encounter_id),
            "ctx_difficulty": float(DIFFICULTY),
            "ctx_partition": float(PARTITION),
            "nc_item_level": 480.0 + index,
            "nc_duration_s": 300.0 + index * 3,
            "c_active_time_pct": 0.5 + (index % 10) / 20.0,
            "c_deaths": float(index % 3),
            "c_total_casts": 40.0 + index,
            "c_mean_uptime": 0.4 + (index % 10) / 25.0,
        },
    )


def _dataset(observations: list[ExperimentalObservation]) -> ExperimentalFeatureDataset:
    return ExperimentalFeatureDataset(observations=tuple(observations))


def _wide_dataset(n_per_group: int = 30) -> ExperimentalFeatureDataset:
    """One spec, one encounter, `n_per_group` sequential observations —
    enough for MODEL_GLOBAL/SPEC/ENCOUNTER to all collapse to the same
    single trainable group under S1's default 75/25 split.
    """
    return _dataset([_obs(i) for i in range(n_per_group)])


def _sparse_dataset() -> ExperimentalFeatureDataset:
    """Many tiny target-level groups — mirrors the real partial campaign's
    shape (max group size well under any reasonable train threshold).
    """
    observations = []
    n = 0
    for spec_index in range(10):
        for _ in range(3):  # 3 obs/target: always below MIN_TRAIN_ROWS_PER_GROUP
            observations.append(_obs(n, spec_name=f"Spec{spec_index}", at_ms=1_000 + n))
            n += 1
    return _dataset(observations)


# -- evaluate_cell: group threshold classification --------------------------------


def test_group_below_threshold_is_marked_insufficient_data() -> None:
    dataset = _sparse_dataset()
    fold = temporal_within_target_split(dataset, validation_fraction=0.3)
    result = evaluate_cell(
        [fold],
        protocol=SplitProtocol.S1_TEMPORAL_WITHIN_TARGET,
        granularity=ModelGranularity.MODEL_TARGET,
        feature_family=FeatureFamily.F1_CONTROLLABLE_ONLY,
        model=ModelKind.BASELINE_0,
        seed=1,
    )
    assert result.status is CellStatus.NOT_EVALUABLE
    assert result.n_groups_trainable == 0
    assert result.n_groups_insufficient > 0
    assert all(g.status is GroupStatus.INSUFFICIENT_DATA for g in result.groups)
    assert "min_train_rows" in (result.reason or "")


def test_group_above_threshold_is_evaluated() -> None:
    dataset = _wide_dataset(40)
    fold = temporal_within_target_split(dataset, validation_fraction=0.3)
    result = evaluate_cell(
        [fold],
        protocol=SplitProtocol.S1_TEMPORAL_WITHIN_TARGET,
        granularity=ModelGranularity.MODEL_GLOBAL,
        feature_family=FeatureFamily.F1_CONTROLLABLE_ONLY,
        model=ModelKind.BASELINE_0,
        seed=1,
    )
    assert result.status is CellStatus.EVALUATED
    assert result.n_groups_trainable == 1
    assert result.evaluation is not None
    assert result.observations_covered > 0


def test_not_evaluable_when_every_fold_is_unusable() -> None:
    dataset = _dataset([_obs(0)])  # a single row: any split degenerates to empty
    fold = temporal_within_target_split(dataset, validation_fraction=0.3)
    result = evaluate_cell(
        [fold],
        protocol=SplitProtocol.S1_TEMPORAL_WITHIN_TARGET,
        granularity=ModelGranularity.MODEL_GLOBAL,
        feature_family=FeatureFamily.F1_CONTROLLABLE_ONLY,
        model=ModelKind.BASELINE_0,
        seed=1,
    )
    assert result.status is CellStatus.NOT_EVALUABLE
    assert "no usable fold" in (result.reason or "")


def test_hierarchical_granularity_is_rejected_explicitly() -> None:
    dataset = _wide_dataset(40)
    fold = temporal_within_target_split(dataset, validation_fraction=0.3)
    try:
        evaluate_cell(
            [fold],
            protocol=SplitProtocol.S1_TEMPORAL_WITHIN_TARGET,
            granularity=ModelGranularity.MODEL_HIERARCHICAL,
            feature_family=FeatureFamily.F1_CONTROLLABLE_ONLY,
            model=ModelKind.BASELINE_0,
            seed=1,
        )
    except NotImplementedError as exc:
        assert "extension point" in str(exc)
    else:
        raise AssertionError("MODEL_HIERARCHICAL must raise, never silently become MODEL_GLOBAL")


def test_pooled_trained_target_ids_come_only_from_training_rows() -> None:
    """Group isolation / temporal isolation: a model trained under
    MODEL_TARGET can only be evaluated where the SAME target had enough
    training rows — `evaluate_split`'s seen/unseen split must never see a
    validation-only target reported as trained.
    """
    dataset = _wide_dataset(40)
    fold = temporal_within_target_split(dataset, validation_fraction=0.3)
    result = evaluate_cell(
        [fold],
        protocol=SplitProtocol.S1_TEMPORAL_WITHIN_TARGET,
        granularity=ModelGranularity.MODEL_GLOBAL,
        feature_family=FeatureFamily.F1_CONTROLLABLE_ONLY,
        model=ModelKind.BASELINE_1,
        seed=1,
    )
    assert result.evaluation is not None
    # every validation row belongs to the one target present, and that
    # target also appears in training (single-target wide dataset) — so it
    # must show up as "seen", never "unseen".
    assert result.evaluation.seen_targets is not None
    assert result.evaluation.unseen_targets is None or result.evaluation.unseen_targets.n == 0


# -- run_matrix ---------------------------------------------------------------------


def test_run_matrix_rejects_hierarchical_granularity() -> None:
    dataset = _wide_dataset(40)
    config = EvaluationConfig(campaign_id="exp-test")
    try:
        run_matrix(dataset, config=config, granularities=(ModelGranularity.MODEL_HIERARCHICAL,))
    except NotImplementedError:
        pass
    else:
        raise AssertionError("run_matrix must reject MODEL_HIERARCHICAL")


def test_run_matrix_is_deterministic_given_the_same_config() -> None:
    dataset = _wide_dataset(40)
    config = EvaluationConfig(campaign_id="exp-test", bootstrap_resamples=50)
    first = run_matrix(
        dataset,
        config=config,
        granularities=(ModelGranularity.MODEL_GLOBAL,),
        splits=(SplitProtocol.S1_TEMPORAL_WITHIN_TARGET,),
        models=(ModelKind.BASELINE_1, ModelKind.LIGHTGBM),
    )
    second = run_matrix(
        dataset,
        config=config,
        granularities=(ModelGranularity.MODEL_GLOBAL,),
        splits=(SplitProtocol.S1_TEMPORAL_WITHIN_TARGET,),
        models=(ModelKind.BASELINE_1, ModelKind.LIGHTGBM),
    )
    first_maes = [c.evaluation.overall.mae for c in first.cells if c.evaluation is not None]
    second_maes = [c.evaluation.overall.mae for c in second.cells if c.evaluation is not None]
    assert first_maes == second_maes
    assert first.dataset_fingerprint == second.dataset_fingerprint


def test_run_matrix_reports_registry_style_dependency_versions() -> None:
    dataset = _wide_dataset(40)
    config = EvaluationConfig(campaign_id="exp-test", bootstrap_resamples=20)
    result = run_matrix(
        dataset,
        config=config,
        granularities=(ModelGranularity.MODEL_GLOBAL,),
        splits=(SplitProtocol.S1_TEMPORAL_WITHIN_TARGET,),
        models=(ModelKind.BASELINE_0,),
    )
    assert "scikit-learn" in result.dependency_versions
    assert "lightgbm" in result.dependency_versions
    assert result.dataset_row_count == 40
    assert result.feature_schema_version


def test_sensitivity_analysis_covers_the_sparse_granularities() -> None:
    dataset = _sparse_dataset()
    config = EvaluationConfig(campaign_id="exp-test", bootstrap_resamples=20)
    result = run_matrix(
        dataset,
        config=config,
        granularities=(ModelGranularity.MODEL_TARGET,),
        splits=(SplitProtocol.S1_TEMPORAL_WITHIN_TARGET,),
        models=(ModelKind.BASELINE_0,),
    )
    target_key = str(ModelGranularity.MODEL_TARGET)
    assert target_key in result.sensitivity
    by_threshold = result.sensitivity[target_key]
    thresholds = sorted(by_threshold)
    # eligible-group count is non-increasing as the threshold rises
    eligible_counts = [by_threshold[t]["n_groups_eligible"] for t in thresholds]
    assert eligible_counts == sorted(eligible_counts, reverse=True)
    # every target group in this fixture has only 2-3 training rows: nothing
    # clears even the lowest threshold (10)
    assert by_threshold[thresholds[0]]["n_groups_eligible"] == 0


# -- dataset fingerprint -------------------------------------------------------------


def test_dataset_fingerprint_is_stable_for_the_same_content() -> None:
    a = _wide_dataset(10)
    b = _wide_dataset(10)
    assert dataset_hash(a) == dataset_hash(b)


def test_dataset_fingerprint_changes_when_content_changes() -> None:
    a = _wide_dataset(10)
    b = _wide_dataset(11)
    assert dataset_hash(a) != dataset_hash(b)


def test_all_implemented_granularities_are_covered_by_run_matrix_default() -> None:
    from botgitgud.phase4.experiment import IMPLEMENTED_GRANULARITIES

    assert set(ALL_GRANULARITIES) == IMPLEMENTED_GRANULARITIES
