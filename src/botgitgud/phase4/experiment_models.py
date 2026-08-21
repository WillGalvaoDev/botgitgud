"""First real models for the statistical architecture experiment: Baseline 1
(linear regression) and the LightGBM candidate, plus the feature space and
bootstrap machinery they share with Baseline 0 (`experiment_metrics.py`).

scikit-learn and LightGBM are optional-extra dependencies (`pyproject.toml`
`[ml]`) — this module is the first one in the project allowed to import
them, now that a real experimental campaign has data to train on. SHAP
stays uninstalled: attribution is out of scope until a model beats the
baselines (docs/fase4-statistical-architecture-experiment.md §3, §10).

Every fitted object here is trained from a single fold's *training* rows
only — `FittedFeatureSpace.fit` decides which columns exist from the
training fold alone, so a column that is constant only in validation can
never silently appear or disappear the feature space a model was fit on.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from sklearn.linear_model import LinearRegression

from botgitgud.phase4.experiment_features import controllable_feature_names, modellable_features
from botgitgud.phase4.experiment_metrics import regression_metrics, spearman
from botgitgud.phase4.experimental_dataset import ExperimentalObservation

DEFAULT_BOOTSTRAP_RESAMPLES = 1000
# Below this many rows, a percentile bootstrap is more noise than signal —
# report "not measurable" rather than a CI nobody should trust.
MIN_ROWS_FOR_BOOTSTRAP = 10


class FeatureFamily(StrEnum):
    """F1 vs F2 ablation (task brief): how much predictive power comes from
    player behavior alone versus context the player does not control.
    """

    F1_CONTROLLABLE_ONLY = "f1_controllable_only"
    F2_FULL_COVARIATES = "f2_full_covariates"


def _candidate_feature_names(family: FeatureFamily) -> tuple[str, ...]:
    if family is FeatureFamily.F1_CONTROLLABLE_ONLY:
        return controllable_feature_names()
    return tuple(spec.name for spec in modellable_features())


@dataclass(frozen=True, slots=True)
class FittedFeatureSpace:
    """Which columns a model may see, decided from the training fold alone.

    A column constant across the whole training fold is dropped: it cannot
    carry information (ctx_difficulty/ctx_partition are always constant for
    a single-partition campaign, and the grouping key's own dimension is
    constant within its own group by construction — MODEL_ENCOUNTER's
    ctx_encounter_id, for instance). Dropping it here, not at the model, is
    what keeps this decision auditable and train-fold-only.
    """

    family: FeatureFamily
    columns: tuple[str, ...]

    @classmethod
    def fit(
        cls, train: Sequence[ExperimentalObservation], family: FeatureFamily
    ) -> FittedFeatureSpace:
        if not train:
            raise ValueError("cannot fit a feature space on an empty training set")
        candidates = _candidate_feature_names(family)
        kept = [name for name in candidates if len({o.features.get(name) for o in train}) > 1]
        return cls(family=family, columns=tuple(kept))

    def transform(self, observations: Sequence[ExperimentalObservation]) -> list[list[float]]:
        return [[o.features.get(name, 0.0) for name in self.columns] for o in observations]


class _ConstantFallback:
    """Used when a fitted feature space has zero columns (every candidate
    feature was constant in that training fold) — degrades to predicting
    the training mean rather than asking sklearn/LightGBM to fit on a
    zero-width matrix. Distinguishable from Baseline 0 only by using the
    mean instead of the median; reported honestly as whichever model kind
    was requested, since the *group* is what lacked feature variance, not
    the model.
    """

    def __init__(self, value: float) -> None:
        self._value = value

    def predict(self, observations: Sequence[ExperimentalObservation]) -> list[float]:
        return [self._value] * len(observations)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


class LinearRegressionModel:
    """Baseline 1 — ordinary least squares over the fitted feature space."""

    def __init__(self, feature_space: FittedFeatureSpace, model: object) -> None:
        self._feature_space = feature_space
        self._model = model

    @classmethod
    def fit(
        cls, train: Sequence[ExperimentalObservation], feature_space: FittedFeatureSpace
    ) -> LinearRegressionModel | _ConstantFallback:
        if not feature_space.columns:
            return _ConstantFallback(_mean([o.y_rank_percent for o in train]))
        x = feature_space.transform(train)
        y = [o.y_rank_percent for o in train]
        model = LinearRegression()
        model.fit(x, y)
        return cls(feature_space, model)

    def predict(self, observations: Sequence[ExperimentalObservation]) -> list[float]:
        x = self._feature_space.transform(observations)
        return [float(v) for v in self._model.predict(x)]  # type: ignore[attr-defined]


class LightGBMModel:
    """The candidate model. Must beat Baseline 1 to justify its complexity
    (task brief) — beating Baseline 0 alone is not evidence of anything but
    the presence of *some* feature, which Baseline 1 already tests.
    """

    def __init__(self, feature_space: FittedFeatureSpace, model: object) -> None:
        self._feature_space = feature_space
        self._model = model

    @classmethod
    def fit(
        cls,
        train: Sequence[ExperimentalObservation],
        feature_space: FittedFeatureSpace,
        *,
        seed: int,
    ) -> LightGBMModel | _ConstantFallback:
        if not feature_space.columns:
            return _ConstantFallback(_mean([o.y_rank_percent for o in train]))
        from lightgbm import LGBMRegressor

        x = feature_space.transform(train)
        y = [o.y_rank_percent for o in train]
        # Conservative and deterministic on purpose (task brief: "não faça
        # tuning extensivo"). min_child_samples is lowered from LightGBM's
        # default of 20 because most experimental groups here are far
        # smaller than that; leaving the default would make LightGBM refuse
        # every split and degrade to predicting the mean for almost every
        # group, silently hiding the comparison this experiment exists to
        # make.
        model = LGBMRegressor(
            n_estimators=200,
            max_depth=4,
            num_leaves=15,
            learning_rate=0.05,
            min_child_samples=5,
            subsample=1.0,
            colsample_bytree=1.0,
            random_state=seed,
            n_jobs=1,
            verbosity=-1,
            force_row_wise=True,
            deterministic=True,
        )
        model.fit(x, y)
        return cls(feature_space, model)

    def predict(self, observations: Sequence[ExperimentalObservation]) -> list[float]:
        x = self._feature_space.transform(observations)
        return [float(v) for v in self._model.predict(x)]  # type: ignore[attr-defined]


@dataclass(frozen=True, slots=True)
class BootstrapCI:
    """A percentile bootstrap interval. `is_measurable` is False rather than
    a fabricated interval when the sample is too small (`MIN_ROWS_FOR_BOOTSTRAP`)
    or the metric itself is undefined on every resample (e.g. Spearman on a
    constant series).
    """

    point_estimate: float | None
    low: float | None
    high: float | None
    n_resamples: int
    is_measurable: bool


def _percentile(sorted_values: Sequence[float], pct: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * pct / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_values[int(k)]
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def bootstrap_mae_spearman(
    y_true: Sequence[float],
    y_pred: Sequence[float],
    *,
    seed: int,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
) -> tuple[BootstrapCI, BootstrapCI]:
    """Resamples (y_true, y_pred) pairs with replacement — never re-fits a
    model per resample (that would be a different, far more expensive
    protocol: this bootstraps the *evaluation*, not the training).
    """
    n = len(y_true)
    if n != len(y_pred):
        raise ValueError("bootstrap_mae_spearman requires equal-length sequences")
    if n < MIN_ROWS_FOR_BOOTSTRAP:
        unmeasurable = BootstrapCI(None, None, None, 0, False)
        return unmeasurable, unmeasurable

    rng = random.Random(seed)
    mae_samples: list[float] = []
    spearman_samples: list[float] = []
    for _ in range(n_resamples):
        idx = [rng.randrange(n) for _ in range(n)]
        t = [y_true[i] for i in idx]
        p = [y_pred[i] for i in idx]
        metrics = regression_metrics(t, p)
        mae_samples.append(metrics.mae)
        rho = spearman(t, p)
        if rho is not None:
            spearman_samples.append(rho)

    mae_samples.sort()
    point_mae = regression_metrics(y_true, y_pred).mae
    mae_ci = BootstrapCI(
        point_mae, _percentile(mae_samples, 2.5), _percentile(mae_samples, 97.5), n_resamples, True
    )

    if len(spearman_samples) < MIN_ROWS_FOR_BOOTSTRAP:
        spearman_ci = BootstrapCI(
            spearman(y_true, y_pred), None, None, len(spearman_samples), False
        )
    else:
        spearman_samples.sort()
        spearman_ci = BootstrapCI(
            spearman(y_true, y_pred),
            _percentile(spearman_samples, 2.5),
            _percentile(spearman_samples, 97.5),
            len(spearman_samples),
            True,
        )
    return mae_ci, spearman_ci
