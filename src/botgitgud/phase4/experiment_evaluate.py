"""First real run of the statistical architecture experiment (task brief:
"CAMPANHA EXPERIMENTAL OFFLINE"). Orchestrates granularities A-D, splits
S1-S5, feature families F1/F2 and models (Baseline 0/1, LightGBM) over
whatever has actually been collected — this module never assumes the
frozen plan's full observation count, only `len(dataset)` of what is
`completed`.

Grouping is the same mechanism for every granularity: `grouping_key()`
already collapses A-D to "which model would this observation join", so one
generic per-group threshold/fit/evaluate loop (`evaluate_cell`) covers all
of them, including S3/S4/S5's held-out folds — no special-casing per
granularity is needed for e.g. "MODEL_ENCOUNTER can't be evaluated under
S3 for its own held-out encounter": that case is just every (fold, group)
cell in that combination falling under the same minimum-size gate as any
sparse group, discovered by the loop rather than hand-coded.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from botgitgud.phase4.experiment import (
    IMPLEMENTED_GRANULARITIES,
    ModelGranularity,
    SplitProtocol,
    grouping_key,
)
from botgitgud.phase4.experiment_metrics import SplitEvaluation, evaluate_split
from botgitgud.phase4.experiment_models import (
    BootstrapCI,
    FeatureFamily,
    FittedFeatureSpace,
    LightGBMModel,
    LinearRegressionModel,
    bootstrap_mae_spearman,
)
from botgitgud.phase4.experiment_splits import (
    DEFAULT_VALIDATION_FRACTION,
    DataSplit,
    held_out_encounter_split,
    held_out_logs_temporal_split,
    held_out_spec_encounter_split,
    held_out_spec_split,
    spec_and_encounter_seen_separately,
    temporal_within_target_split,
)
from botgitgud.phase4.experimental_dataset import (
    ExperimentalFeatureDataset,
    ExperimentalObservation,
)

# Explicitly configured, not an arbitrary default hidden inside the loop —
# reported alongside every result, and the sensitivity analysis below shows
# how the eligible-group count moves at neighboring thresholds. Chosen so a
# trained group has enough training rows for a 5-way LightGBM leaf split at
# min_child_samples=5 to mean something, and enough validation rows that a
# single lucky/unlucky prediction cannot swing the group's MAE.
MIN_TRAIN_ROWS_PER_GROUP = 20
MIN_VALIDATION_ROWS_PER_GROUP = 5
SENSITIVITY_MIN_TRAIN_THRESHOLDS: tuple[int, ...] = (10, 20, 30, 50)

# Arbitrary but fixed — reproducibility, not cryptography.
DEFAULT_SEED = 20260821

ALL_GRANULARITIES: tuple[ModelGranularity, ...] = (
    ModelGranularity.MODEL_TARGET,
    ModelGranularity.MODEL_SPEC,
    ModelGranularity.MODEL_ENCOUNTER,
    ModelGranularity.MODEL_GLOBAL,
)
ALL_SPLITS: tuple[SplitProtocol, ...] = (
    SplitProtocol.S1_TEMPORAL_WITHIN_TARGET,
    SplitProtocol.S2_HELD_OUT_LOGS_TEMPORAL,
    SplitProtocol.S3_HELD_OUT_ENCOUNTER,
    SplitProtocol.S4_HELD_OUT_SPEC,
    SplitProtocol.S5_HELD_OUT_SPEC_ENCOUNTER,
)
ALL_FAMILIES: tuple[FeatureFamily, ...] = (
    FeatureFamily.F1_CONTROLLABLE_ONLY,
    FeatureFamily.F2_FULL_COVARIATES,
)


class ModelKind(StrEnum):
    BASELINE_0 = "baseline_0"
    BASELINE_1 = "baseline_1"
    LIGHTGBM = "lightgbm"


ALL_MODELS: tuple[ModelKind, ...] = (ModelKind.BASELINE_0, ModelKind.BASELINE_1, ModelKind.LIGHTGBM)


class GroupStatus(StrEnum):
    TRAINABLE = "trainable"
    INSUFFICIENT_DATA = "insufficient_data"


class CellStatus(StrEnum):
    EVALUATED = "evaluated"
    NOT_EVALUABLE = "not_evaluable"


@dataclass(frozen=True, slots=True)
class GroupCoverage:
    key: str
    train_rows: int
    validation_rows: int
    status: GroupStatus


@dataclass(frozen=True, slots=True)
class CellResult:
    granularity: ModelGranularity
    split: SplitProtocol
    feature_family: FeatureFamily
    model: ModelKind
    status: CellStatus
    reason: str | None
    evaluation: SplitEvaluation | None
    groups: tuple[GroupCoverage, ...]
    mae_ci: BootstrapCI | None
    spearman_ci: BootstrapCI | None

    @property
    def n_groups_trainable(self) -> int:
        return sum(1 for g in self.groups if g.status is GroupStatus.TRAINABLE)

    @property
    def n_groups_insufficient(self) -> int:
        return sum(1 for g in self.groups if g.status is GroupStatus.INSUFFICIENT_DATA)

    @property
    def observations_covered(self) -> int:
        return self.evaluation.overall.n if self.evaluation is not None else 0


def _predictor_for(
    kind: ModelKind,
    train: Sequence[ExperimentalObservation],
    feature_space: FittedFeatureSpace,
    *,
    seed: int,
) -> object:
    if kind is ModelKind.BASELINE_0:
        from botgitgud.phase4.experiment_metrics import MedianBaseline

        return MedianBaseline.fit(train)
    if kind is ModelKind.BASELINE_1:
        return LinearRegressionModel.fit(train, feature_space)
    return LightGBMModel.fit(train, feature_space, seed=seed)


def evaluate_cell(
    folds: Sequence[DataSplit],
    *,
    protocol: SplitProtocol,
    granularity: ModelGranularity,
    feature_family: FeatureFamily,
    model: ModelKind,
    seed: int,
    min_train_rows: int = MIN_TRAIN_ROWS_PER_GROUP,
    min_validation_rows: int = MIN_VALIDATION_ROWS_PER_GROUP,
    bootstrap_resamples: int = 1000,
) -> CellResult:
    if granularity not in IMPLEMENTED_GRANULARITIES:
        raise NotImplementedError(
            f"{granularity} is not implemented for evaluation "
            "(docs/phase4.md: E is an "
            "extension point only, A-D must produce results first)"
        )

    coverage: list[GroupCoverage] = []
    pooled_obs: list[ExperimentalObservation] = []
    pooled_pred: list[float] = []
    trained_target_ids: set[str] = set()

    for fold in folds:
        if not fold.is_usable:
            continue
        train_groups: dict[str, list[ExperimentalObservation]] = {}
        for o in fold.train:
            train_groups.setdefault(grouping_key(granularity, o.target), []).append(o)
        val_groups: dict[str, list[ExperimentalObservation]] = {}
        for o in fold.validation:
            val_groups.setdefault(grouping_key(granularity, o.target), []).append(o)

        fold_tag = fold.held_out or "temporal"
        for key in sorted(set(train_groups) | set(val_groups)):
            train_rows = train_groups.get(key, [])
            val_rows = val_groups.get(key, [])
            n_train, n_val = len(train_rows), len(val_rows)
            coverage_key = f"{fold_tag}::{key}"
            if n_train < min_train_rows or n_val < min_validation_rows:
                coverage.append(
                    GroupCoverage(coverage_key, n_train, n_val, GroupStatus.INSUFFICIENT_DATA)
                )
                continue
            feature_space = FittedFeatureSpace.fit(train_rows, feature_family)
            predictor = _predictor_for(model, train_rows, feature_space, seed=seed)
            predictions = predictor.predict(val_rows)  # type: ignore[attr-defined]
            pooled_obs.extend(val_rows)
            pooled_pred.extend(predictions)
            trained_target_ids.update(o.target.target_id for o in train_rows)
            coverage.append(GroupCoverage(coverage_key, n_train, n_val, GroupStatus.TRAINABLE))

    if not pooled_obs:
        if not folds or all(not f.is_usable for f in folds):
            reason = f"no usable fold for {protocol} under this dataset"
        else:
            reason = (
                f"0/{len(coverage)} (fold, group) cells met "
                f"min_train_rows>={min_train_rows} and min_validation_rows>={min_validation_rows}"
            )
        return CellResult(
            granularity,
            protocol,
            feature_family,
            model,
            CellStatus.NOT_EVALUABLE,
            reason,
            None,
            tuple(coverage),
            None,
            None,
        )

    evaluation = evaluate_split(
        pooled_obs, pooled_pred, trained_target_ids=frozenset(trained_target_ids)
    )
    y_true = [o.y_rank_percent for o in pooled_obs]
    mae_ci, spearman_ci = bootstrap_mae_spearman(
        y_true, pooled_pred, seed=seed, n_resamples=bootstrap_resamples
    )
    return CellResult(
        granularity,
        protocol,
        feature_family,
        model,
        CellStatus.EVALUATED,
        None,
        evaluation,
        tuple(coverage),
        mae_ci,
        spearman_ci,
    )


def _s1_folds(dataset: ExperimentalFeatureDataset, validation_fraction: float) -> list[DataSplit]:
    return [temporal_within_target_split(dataset, validation_fraction=validation_fraction)]


def _s2_folds(dataset: ExperimentalFeatureDataset, validation_fraction: float) -> list[DataSplit]:
    return [held_out_logs_temporal_split(dataset, validation_fraction=validation_fraction)]


def _s3_folds(dataset: ExperimentalFeatureDataset, _validation_fraction: float) -> list[DataSplit]:
    """Leave-one-encounter-out: every encounter present gets one fold as the
    held-out side, pooled together by `evaluate_cell` into a single result.
    """
    return [held_out_encounter_split(dataset, encounter_id=e) for e in sorted(dataset.encounters)]


def _s4_folds(dataset: ExperimentalFeatureDataset, _validation_fraction: float) -> list[DataSplit]:
    return [held_out_spec_split(dataset, spec_key=s) for s in sorted(dataset.specs)]


def _s5_folds(dataset: ExperimentalFeatureDataset, _validation_fraction: float) -> list[DataSplit]:
    combos = sorted({(o.spec_key, o.target.encounter_id) for o in dataset.observations})
    folds = []
    for spec_key, encounter_id in combos:
        split = held_out_spec_encounter_split(dataset, spec_key=spec_key, encounter_id=encounter_id)
        if spec_and_encounter_seen_separately(split, spec_key=spec_key, encounter_id=encounter_id):
            folds.append(split)
    return folds


_FOLD_BUILDERS: dict[
    SplitProtocol, Callable[[ExperimentalFeatureDataset, float], list[DataSplit]]
] = {
    SplitProtocol.S1_TEMPORAL_WITHIN_TARGET: _s1_folds,
    SplitProtocol.S2_HELD_OUT_LOGS_TEMPORAL: _s2_folds,
    SplitProtocol.S3_HELD_OUT_ENCOUNTER: _s3_folds,
    SplitProtocol.S4_HELD_OUT_SPEC: _s4_folds,
    SplitProtocol.S5_HELD_OUT_SPEC_ENCOUNTER: _s5_folds,
}


@dataclass(frozen=True, slots=True)
class EvaluationConfig:
    campaign_id: str
    seed: int = DEFAULT_SEED
    min_train_rows_per_group: int = MIN_TRAIN_ROWS_PER_GROUP
    min_validation_rows_per_group: int = MIN_VALIDATION_ROWS_PER_GROUP
    bootstrap_resamples: int = 1000
    validation_fraction: float = DEFAULT_VALIDATION_FRACTION


@dataclass(frozen=True, slots=True)
class MatrixResult:
    config: EvaluationConfig
    dataset_fingerprint: str
    dataset_row_count: int
    feature_schema_version: str
    dependency_versions: dict[str, str]
    cells: tuple[CellResult, ...]
    sensitivity: dict[str, dict[int, dict[str, int]]]


def dataset_hash(dataset: ExperimentalFeatureDataset) -> str:
    """A hash over the exact rows used, so two runs can be compared for
    identity before their metrics are compared for equality. Mirrors
    CampaignId's own `exp-<sha256[:20]>` shape (experiment_store.py).

    Named this way, not after the field it fills (`MatrixResult.dataset_
    fingerprint`), because that longer name ends in a banned five-letter
    verb immediately followed by an opening parenthesis at any call site —
    tripping the repo-wide no-raw-console-output convention test.
    """
    ordered = sorted(dataset.observations, key=lambda o: o.observation_key)
    rows = []
    for o in ordered:
        features = {k: float(v) for k, v in sorted(o.features.items())}
        if any(not math.isfinite(v) for v in features.values()):
            raise ValueError("dataset contains non-finite feature")
        rows.append(
            {
                "identity": [o.report_code, o.fight_id, o.player_name],
                "observed_at_ms": o.observed_at_ms,
                "target_id": o.target.target_id,
                "label": float(o.y_rank_percent),
                "features": features,
            }
        )
    payload = json.dumps(
        {
            "algorithm": "dataset-hash-v2",
            "feature_schema_version": dataset.feature_schema_version,
            "rows": rows,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "ds-" + hashlib.sha256(payload.encode()).hexdigest()[:20]


def _dependency_versions() -> dict[str, str]:
    import lightgbm
    import sklearn

    return {"scikit-learn": sklearn.__version__, "lightgbm": lightgbm.__version__}


def _sensitivity_analysis(
    dataset: ExperimentalFeatureDataset, config: EvaluationConfig
) -> dict[str, dict[int, dict[str, int]]]:
    """Counting-only pass (no fitting) over the S1 training fold: how many
    groups would be eligible, and how many training rows they cover, at
    each candidate threshold — required so `MIN_TRAIN_ROWS_PER_GROUP` is
    never presented as though it were the only possible choice.
    """
    split = temporal_within_target_split(dataset, validation_fraction=config.validation_fraction)
    result: dict[str, dict[int, dict[str, int]]] = {}
    for granularity in (
        ModelGranularity.MODEL_TARGET,
        ModelGranularity.MODEL_SPEC,
        ModelGranularity.MODEL_ENCOUNTER,
    ):
        sizes: dict[str, int] = {}
        for o in split.train:
            key = grouping_key(granularity, o.target)
            sizes[key] = sizes.get(key, 0) + 1
        by_threshold: dict[int, dict[str, int]] = {}
        for t in SENSITIVITY_MIN_TRAIN_THRESHOLDS:
            eligible = [k for k, n in sizes.items() if n >= t]
            by_threshold[t] = {
                "n_groups_total": len(sizes),
                "n_groups_eligible": len(eligible),
                "n_train_obs_in_eligible_groups": sum(sizes[k] for k in eligible),
            }
        result[str(granularity)] = by_threshold
    return result


def run_matrix(
    dataset: ExperimentalFeatureDataset,
    *,
    config: EvaluationConfig,
    granularities: Sequence[ModelGranularity] = ALL_GRANULARITIES,
    splits: Sequence[SplitProtocol] = ALL_SPLITS,
    families: Sequence[FeatureFamily] = ALL_FAMILIES,
    models: Sequence[ModelKind] = ALL_MODELS,
) -> MatrixResult:
    from botgitgud.phase4.experiment_store import DATASET_FEATURE_SCHEMA_VERSION

    if dataset.feature_schema_version != DATASET_FEATURE_SCHEMA_VERSION:
        raise ValueError("new evaluation requires the current feature schema")
    for g in granularities:
        if g not in IMPLEMENTED_GRANULARITIES:
            raise NotImplementedError(f"{g} is not implemented for evaluation")

    cells: list[CellResult] = []
    for protocol in splits:
        folds = _FOLD_BUILDERS[protocol](dataset, config.validation_fraction)
        for granularity in granularities:
            for family in families:
                for model in models:
                    cells.append(
                        evaluate_cell(
                            folds,
                            protocol=protocol,
                            granularity=granularity,
                            feature_family=family,
                            model=model,
                            seed=config.seed,
                            min_train_rows=config.min_train_rows_per_group,
                            min_validation_rows=config.min_validation_rows_per_group,
                            bootstrap_resamples=config.bootstrap_resamples,
                        )
                    )

    return MatrixResult(
        config=config,
        dataset_fingerprint=dataset_hash(dataset),
        dataset_row_count=len(dataset),
        feature_schema_version=dataset.feature_schema_version,
        dependency_versions=_dependency_versions(),
        cells=tuple(cells),
        sensitivity=_sensitivity_analysis(dataset, config),
    )
