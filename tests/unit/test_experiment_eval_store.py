from __future__ import annotations

from pathlib import Path

from botgitgud.domain.specs import SpecId
from botgitgud.ingest.store import Store
from botgitgud.phase4.experiment import ModelGranularity, SplitProtocol
from botgitgud.phase4.experiment_classify import ClassificationResult, SignalClassification
from botgitgud.phase4.experiment_eval_store import ExperimentArchitectureEvalStore
from botgitgud.phase4.experiment_evaluate import (
    CellResult,
    CellStatus,
    EvaluationConfig,
    GroupCoverage,
    GroupStatus,
    MatrixResult,
    ModelKind,
)
from botgitgud.phase4.experiment_metrics import RegressionMetrics, SplitEvaluation
from botgitgud.phase4.experiment_models import FeatureFamily
from botgitgud.phase4.registry import ModelStatus, Phase4ModelRecord, Phase4ModelRegistry
from botgitgud.phase4.target import Phase4Target

CAMPAIGN_ID = "exp-teststore0000000000"


def _matrix_result() -> MatrixResult:
    metrics = RegressionMetrics(n=50, mae=8.0, rmse=9.0, r2=0.4, spearman=0.5)
    evaluation = SplitEvaluation(overall=metrics)
    cell = CellResult(
        granularity=ModelGranularity.MODEL_GLOBAL,
        split=SplitProtocol.S1_TEMPORAL_WITHIN_TARGET,
        feature_family=FeatureFamily.F2_FULL_COVARIATES,
        model=ModelKind.LIGHTGBM,
        status=CellStatus.EVALUATED,
        reason=None,
        evaluation=evaluation,
        groups=(GroupCoverage("global", 40, 10, GroupStatus.TRAINABLE),),
        mae_ci=None,
        spearman_ci=None,
    )
    not_evaluable = CellResult(
        granularity=ModelGranularity.MODEL_TARGET,
        split=SplitProtocol.S1_TEMPORAL_WITHIN_TARGET,
        feature_family=FeatureFamily.F1_CONTROLLABLE_ONLY,
        model=ModelKind.BASELINE_0,
        status=CellStatus.NOT_EVALUABLE,
        reason="0 groups met the threshold",
        evaluation=None,
        groups=(GroupCoverage("t1", 3, 1, GroupStatus.INSUFFICIENT_DATA),),
        mae_ci=None,
        spearman_ci=None,
    )
    return MatrixResult(
        config=EvaluationConfig(campaign_id=CAMPAIGN_ID, bootstrap_resamples=10),
        dataset_fingerprint="ds-abc123",
        dataset_row_count=603,
        feature_schema_version="sae3-v1",
        dependency_versions={"scikit-learn": "1.9.0", "lightgbm": "4.7.0"},
        cells=(cell, not_evaluable),
        sensitivity={"model_target": {10: {"n_groups_total": 5, "n_groups_eligible": 0}}},
    )


def _classification() -> ClassificationResult:
    return ClassificationResult(SignalClassification.MORE_DATA_NEEDED, "synthetic justification")


def test_save_and_list_round_trips(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        eval_store = ExperimentArchitectureEvalStore(store)
        eval_store.save("eval-001", _matrix_result(), _classification())
        runs = eval_store.list_runs(CAMPAIGN_ID)
    assert len(runs) == 1
    assert runs[0]["run_id"] == "eval-001"
    assert runs[0]["dataset_fingerprint"] == "ds-abc123"
    assert runs[0]["dataset_row_count"] == 603
    assert runs[0]["classification"] == "more_data_needed"


def test_save_is_idempotent_for_the_same_run_id(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        eval_store = ExperimentArchitectureEvalStore(store)
        eval_store.save("eval-001", _matrix_result(), _classification())
        eval_store.save("eval-001", _matrix_result(), _classification())
        runs = eval_store.list_runs(CAMPAIGN_ID)
    assert len(runs) == 1


def test_list_runs_only_returns_the_requested_campaign(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        eval_store = ExperimentArchitectureEvalStore(store)
        eval_store.save("eval-001", _matrix_result(), _classification())
        assert eval_store.list_runs("exp-someothercampaign0") == []


def test_eval_store_and_model_registry_are_fully_isolated(tmp_path: Path) -> None:
    """Task brief hard requirement: an evaluation run can never touch
    `phase4_model_registry`, and nothing in the registry leaks into the
    eval store's table — separate tables, separate concerns.
    """
    with Store(tmp_path) as store:
        eval_store = ExperimentArchitectureEvalStore(store)
        registry = Phase4ModelRegistry(store)

        eval_store.save("eval-001", _matrix_result(), _classification())

        target = Phase4Target(SpecId("Mage", "Frost"), 3176, 5, 4)
        registry.register(Phase4ModelRecord(target=target, status=ModelStatus.COLLECTING))

        # the eval run never registered anything, and stays UNAVAILABLE-free:
        assert registry.get(target) is not None
        assert registry.get(target).status is ModelStatus.COLLECTING  # type: ignore[union-attr]
        assert len(registry.list_all()) == 1

        # the registry write never touched the eval store's table:
        runs = eval_store.list_runs(CAMPAIGN_ID)
        assert len(runs) == 1

        tables = {
            row[0]
            for row in store.execute_returning(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name IN ('phase4_model_registry', 'experiment_architecture_eval_runs')"
            )
        }
        assert tables == {"phase4_model_registry", "experiment_architecture_eval_runs"}


def test_no_model_from_an_eval_run_is_ever_ready_in_the_registry(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        eval_store = ExperimentArchitectureEvalStore(store)
        eval_store.save("eval-001", _matrix_result(), _classification())
        registry = Phase4ModelRegistry(store)
        assert registry.list_all() == []
