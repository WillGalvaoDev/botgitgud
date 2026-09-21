from __future__ import annotations

from botgitgud.phase4.experiment import ModelGranularity, SplitProtocol
from botgitgud.phase4.experiment_classify import SignalClassification, classify_signal
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
from botgitgud.phase4.experiment_models import BootstrapCI, FeatureFamily


def _cell(
    *,
    granularity: ModelGranularity,
    family: FeatureFamily,
    model: ModelKind,
    mae: float,
    rho: float | None,
    n: int = 50,
    status: CellStatus = CellStatus.EVALUATED,
    measurable_ci: bool = True,
) -> CellResult:
    evaluation = None
    ci = None
    if status is CellStatus.EVALUATED:
        metrics = RegressionMetrics(n=n, mae=mae, rmse=mae, r2=0.1, spearman=rho)
        evaluation = SplitEvaluation(overall=metrics)
        ci = BootstrapCI(mae, mae - 1, mae + 1, 100, measurable_ci)
    return CellResult(
        granularity=granularity,
        split=SplitProtocol.S1_TEMPORAL_WITHIN_TARGET,
        feature_family=family,
        model=model,
        status=status,
        reason=None if status is CellStatus.EVALUATED else "synthetic",
        evaluation=evaluation,
        groups=(GroupCoverage("k", n, n, GroupStatus.TRAINABLE),),
        mae_ci=ci,
        spearman_ci=ci,
    )


def _matrix(cells: list[CellResult]) -> MatrixResult:
    return MatrixResult(
        config=EvaluationConfig(campaign_id="exp-test"),
        dataset_fingerprint="ds-test",
        dataset_row_count=100,
        feature_schema_version="sae3-v1",
        dependency_versions={},
        cells=tuple(cells),
        sensitivity={},
    )


def test_classify_architectural_failure_when_nothing_beats_baseline0() -> None:
    cells = [
        _cell(
            granularity=ModelGranularity.MODEL_GLOBAL,
            family=FeatureFamily.F1_CONTROLLABLE_ONLY,
            model=ModelKind.BASELINE_0,
            mae=10.0,
            rho=0.0,
        ),
        _cell(
            granularity=ModelGranularity.MODEL_GLOBAL,
            family=FeatureFamily.F1_CONTROLLABLE_ONLY,
            model=ModelKind.BASELINE_1,
            mae=12.0,
            rho=0.05,
        ),  # worse MAE: not a "beat"
    ]
    result = classify_signal(_matrix(cells))
    assert result.classification is SignalClassification.ARCHITECTURAL_FAILURE


def test_classify_more_data_needed_when_coverage_is_thin() -> None:
    cells = [
        _cell(
            granularity=ModelGranularity.MODEL_GLOBAL,
            family=FeatureFamily.F1_CONTROLLABLE_ONLY,
            model=ModelKind.BASELINE_0,
            mae=10.0,
            rho=0.0,
        ),
        _cell(
            granularity=ModelGranularity.MODEL_GLOBAL,
            family=FeatureFamily.F1_CONTROLLABLE_ONLY,
            model=ModelKind.LIGHTGBM,
            mae=8.0,
            rho=0.3,
        ),
    ] + [
        _cell(
            granularity=ModelGranularity.MODEL_TARGET,
            family=FeatureFamily.F1_CONTROLLABLE_ONLY,
            model=ModelKind.BASELINE_0,
            mae=0.0,
            rho=None,
            status=CellStatus.NOT_EVALUABLE,
        )
        for _ in range(6)
    ]
    result = classify_signal(_matrix(cells))
    assert result.classification is SignalClassification.MORE_DATA_NEEDED


def test_classify_sufficient_signal_with_good_coverage_and_a_measurable_win() -> None:
    cells = [
        _cell(
            granularity=ModelGranularity.MODEL_GLOBAL,
            family=FeatureFamily.F1_CONTROLLABLE_ONLY,
            model=ModelKind.BASELINE_0,
            mae=10.0,
            rho=0.0,
        ),
        _cell(
            granularity=ModelGranularity.MODEL_GLOBAL,
            family=FeatureFamily.F1_CONTROLLABLE_ONLY,
            model=ModelKind.LIGHTGBM,
            mae=6.0,
            rho=0.5,
        ),
    ]
    result = classify_signal(_matrix(cells))
    assert result.classification is SignalClassification.SUFFICIENT_SIGNAL


def test_classify_ignores_cells_outside_s1() -> None:
    """Only S1 cells decide the classification — a strong S3/S4 result must
    never substitute for an actual S1 signal check.
    """
    s1_cell = _cell(
        granularity=ModelGranularity.MODEL_GLOBAL,
        family=FeatureFamily.F1_CONTROLLABLE_ONLY,
        model=ModelKind.BASELINE_0,
        mae=10.0,
        rho=0.0,
    )
    s3_only_win = CellResult(
        granularity=ModelGranularity.MODEL_GLOBAL,
        split=SplitProtocol.S3_HELD_OUT_ENCOUNTER,
        feature_family=FeatureFamily.F1_CONTROLLABLE_ONLY,
        model=ModelKind.LIGHTGBM,
        status=CellStatus.EVALUATED,
        reason=None,
        evaluation=SplitEvaluation(
            overall=RegressionMetrics(n=50, mae=1.0, rmse=1.0, r2=0.9, spearman=0.9)
        ),
        groups=(GroupCoverage("k", 50, 50, GroupStatus.TRAINABLE),),
        mae_ci=BootstrapCI(1.0, 0.5, 1.5, 100, True),
        spearman_ci=BootstrapCI(0.9, 0.8, 1.0, 100, True),
    )
    result = classify_signal(_matrix([s1_cell, s3_only_win]))
    assert result.classification is SignalClassification.ARCHITECTURAL_FAILURE
