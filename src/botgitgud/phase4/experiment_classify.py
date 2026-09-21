"""Task-brief decision classification for one architecture-evaluation run:
SUFFICIENT_SIGNAL / MORE_DATA_NEEDED / ARCHITECTURAL_FAILURE.

Split out of `experiment_evaluate.py` (which computes the matrix) because
this is a distinct concern — deciding what the measured cells *mean* — and
keeps that module under the project's line-count convention. This is an
experimental-campaign decision only, never a Fase 4 gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from botgitgud.phase4.experiment import ModelGranularity, SplitProtocol
from botgitgud.phase4.experiment_evaluate import CellStatus, MatrixResult, ModelKind
from botgitgud.phase4.experiment_models import FeatureFamily


class SignalClassification(StrEnum):
    SUFFICIENT_SIGNAL = "sufficient_signal"
    MORE_DATA_NEEDED = "more_data_needed"
    ARCHITECTURAL_FAILURE = "architectural_failure"


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    classification: SignalClassification
    justification: str


def classify_signal(result: MatrixResult) -> ClassificationResult:
    """Quantitative, from the measured cells only:

    1. ARCHITECTURAL_FAILURE if nothing anywhere beats Baseline 0 on S1 on
       both MAE and Spearman — H0 is not rejected, so more rows would not
       change the question being asked.
    2. Otherwise MORE_DATA_NEEDED if fewer than half of all requested cells
       were EVALUATED, or if none of the winning comparisons have a
       measurable (non-degenerate) bootstrap interval.
    3. Otherwise SUFFICIENT_SIGNAL.
    """
    s1_cells = [c for c in result.cells if c.split is SplitProtocol.S1_TEMPORAL_WITHIN_TARGET]
    s1_evaluated = [c for c in s1_cells if c.status is CellStatus.EVALUATED]

    beats: list[tuple[ModelGranularity, FeatureFamily, ModelKind]] = []
    for c in s1_evaluated:
        if c.model is ModelKind.BASELINE_0 or c.evaluation is None:
            continue
        baseline = next(
            (
                b
                for b in s1_evaluated
                if b.model is ModelKind.BASELINE_0
                and b.granularity is c.granularity
                and b.feature_family is c.feature_family
                and b.evaluation is not None
            ),
            None,
        )
        if baseline is None or baseline.evaluation is None:
            continue
        base_mae = baseline.evaluation.overall.mae
        base_rho = baseline.evaluation.overall.spearman or 0.0
        cand_mae = c.evaluation.overall.mae
        cand_rho = c.evaluation.overall.spearman or 0.0
        if cand_mae < base_mae and cand_rho > base_rho:
            beats.append((c.granularity, c.feature_family, c.model))

    if not beats:
        return ClassificationResult(
            SignalClassification.ARCHITECTURAL_FAILURE,
            f"0/{len(s1_evaluated)} evaluated S1 cells beat Baseline 0 on both MAE and "
            "Spearman — H0 (no predictive signal) is not rejected for any granularity or "
            "feature family this dataset could evaluate.",
        )

    n_evaluated = sum(1 for c in result.cells if c.status is CellStatus.EVALUATED)
    coverage_ratio = n_evaluated / len(result.cells) if result.cells else 0.0
    measurable_wins = [
        c
        for c in s1_evaluated
        if (c.granularity, c.feature_family, c.model) in beats
        and c.mae_ci is not None
        and c.mae_ci.is_measurable
    ]

    if coverage_ratio < 0.5 or not measurable_wins:
        return ClassificationResult(
            SignalClassification.MORE_DATA_NEEDED,
            f"{len(beats)} S1 cell(s) beat Baseline 0, but only {n_evaluated}/{len(result.cells)} "
            f"requested cells ({coverage_ratio:.0%}) were evaluable and "
            f"{len(measurable_wins)} winning cell(s) have a measurable bootstrap interval — "
            "coverage/precision is too thin to decide an architecture yet.",
        )

    return ClassificationResult(
        SignalClassification.SUFFICIENT_SIGNAL,
        f"{len(beats)} S1 cell(s) beat Baseline 0 with a measurable bootstrap interval, and "
        f"{n_evaluated}/{len(result.cells)} requested cells ({coverage_ratio:.0%}) were evaluable.",
    )
