"""SAE.5 — comparable metrics for the architecture experiment
(docs/fase4-statistical-architecture-experiment.md §11-§12).

Pure Python on purpose: numpy is not a project dependency (§1.2 forbids
undeclared ones) and these formulas are short enough that adding one would
buy nothing. LightGBM stays uninstalled until the experiment actually runs;
`Predictor` is the seam it will plug into.

Spearman matters more than MAE for how the product actually uses a model:
the report *ranks* recommendations, so getting the ordering right is worth
more than hitting the absolute percentile.

Nothing here interprets a coefficient or an importance as causal (§3).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from botgitgud.phase4.experiment import percentile_bucket
from botgitgud.phase4.experimental_dataset import ExperimentalObservation


class Predictor(Protocol):
    """The seam for Baseline 0, Baseline 1 and LightGBM alike."""

    def predict(self, observations: Sequence[ExperimentalObservation]) -> list[float]: ...


@dataclass(frozen=True, slots=True)
class MedianBaseline:
    """Baseline 0 — predict the training median, always.

    The rejection floor for H0: a model that cannot beat this has no
    predictive power, and the granularity question does not even arise.
    """

    value: float

    @classmethod
    def fit(cls, observations: Sequence[ExperimentalObservation]) -> MedianBaseline:
        if not observations:
            raise ValueError("cannot fit a baseline on an empty training set")
        return cls(value=_median([o.y_rank_percent for o in observations]))

    def predict(self, observations: Sequence[ExperimentalObservation]) -> list[float]:
        return [self.value] * len(observations)


def _median(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("median of an empty sequence")
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _average_ranks(values: Sequence[float]) -> list[float]:
    """Ranks with ties averaged — required for Spearman to be correct when
    several observations share a percentile, which is common at 0 and 100.
    """
    indexed = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(indexed):
        end = position
        while end + 1 < len(indexed) and values[indexed[end + 1]] == values[indexed[position]]:
            end += 1
        average = (position + end) / 2.0 + 1.0
        for index in indexed[position : end + 1]:
            ranks[index] = average
        position = end + 1
    return ranks


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    denominator = math.sqrt(sum(v * v for v in dx)) * math.sqrt(sum(v * v for v in dy))
    if denominator == 0.0:
        return None  # a constant series has no defined correlation
    return sum(a * b for a, b in zip(dx, dy, strict=True)) / denominator


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Rank correlation. None when undefined (fewer than 2 points, or one
    side constant) — never silently 0.0, which would read as "no relation"
    rather than "not measurable".
    """
    if len(xs) != len(ys):
        raise ValueError("spearman requires equal-length sequences")
    if len(xs) < 2:
        return None
    return _pearson(_average_ranks(xs), _average_ranks(ys))


@dataclass(frozen=True, slots=True)
class RegressionMetrics:
    n: int
    mae: float
    rmse: float
    r2: float | None
    spearman: float | None

    @property
    def is_measurable(self) -> bool:
        return self.n > 0


def regression_metrics(y_true: Sequence[float], y_pred: Sequence[float]) -> RegressionMetrics:
    if len(y_true) != len(y_pred):
        raise ValueError("regression_metrics requires equal-length sequences")
    n = len(y_true)
    if n == 0:
        return RegressionMetrics(n=0, mae=0.0, rmse=0.0, r2=None, spearman=None)

    errors = [t - p for t, p in zip(y_true, y_pred, strict=True)]
    mae = sum(abs(e) for e in errors) / n
    rmse = math.sqrt(sum(e * e for e in errors) / n)

    mean_true = sum(y_true) / n
    ss_tot = sum((t - mean_true) ** 2 for t in y_true)
    ss_res = sum(e * e for e in errors)
    # A constant truth column makes R^2 undefined rather than 0 or 1.
    r2 = None if ss_tot == 0.0 else 1.0 - ss_res / ss_tot

    return RegressionMetrics(n=n, mae=mae, rmse=rmse, r2=r2, spearman=spearman(y_true, y_pred))


@dataclass(frozen=True, slots=True)
class SplitEvaluation:
    """Everything §11 requires for one (architecture, split) cell."""

    overall: RegressionMetrics
    by_bucket: dict[str, RegressionMetrics] = field(default_factory=dict)
    by_spec: dict[str, RegressionMetrics] = field(default_factory=dict)
    by_encounter: dict[int, RegressionMetrics] = field(default_factory=dict)
    seen_targets: RegressionMetrics | None = None
    unseen_targets: RegressionMetrics | None = None


def _grouped(
    observations: Sequence[ExperimentalObservation],
    predictions: Sequence[float],
    key: Callable[[ExperimentalObservation], object],
) -> dict[object, RegressionMetrics]:
    groups: dict[object, tuple[list[float], list[float]]] = {}
    for observation, prediction in zip(observations, predictions, strict=True):
        truth, predicted = groups.setdefault(key(observation), ([], []))
        truth.append(observation.y_rank_percent)
        predicted.append(prediction)
    return {k: regression_metrics(t, p) for k, (t, p) in groups.items()}


def evaluate_split(
    observations: Sequence[ExperimentalObservation],
    predictions: Sequence[float],
    *,
    trained_target_ids: frozenset[str] = frozenset(),
) -> SplitEvaluation:
    """`trained_target_ids` is what separates a shared model's performance on
    targets it saw from targets it never saw — the whole point of B/C/D/E.
    """
    if len(observations) != len(predictions):
        raise ValueError("evaluate_split requires equal-length sequences")

    y_true = [o.y_rank_percent for o in observations]
    seen_pairs = [
        (o, p)
        for o, p in zip(observations, predictions, strict=True)
        if o.target.target_id in trained_target_ids
    ]
    unseen_pairs = [
        (o, p)
        for o, p in zip(observations, predictions, strict=True)
        if o.target.target_id not in trained_target_ids
    ]

    def _metrics(pairs: list[tuple[ExperimentalObservation, float]]) -> RegressionMetrics | None:
        if not pairs:
            return None
        return regression_metrics([o.y_rank_percent for o, _ in pairs], [p for _, p in pairs])

    return SplitEvaluation(
        overall=regression_metrics(y_true, predictions),
        by_bucket={
            str(k): v
            for k, v in _grouped(
                observations, predictions, lambda o: percentile_bucket(o.y_rank_percent)
            ).items()
        },
        by_spec={
            str(k): v for k, v in _grouped(observations, predictions, lambda o: o.spec_key).items()
        },
        by_encounter={
            int(k): v  # type: ignore[arg-type]
            for k, v in _grouped(observations, predictions, lambda o: o.target.encounter_id).items()
        },
        seen_targets=_metrics(seen_pairs),
        unseen_targets=_metrics(unseen_pairs),
    )


@dataclass(frozen=True, slots=True)
class ArchitectureComparison:
    """§12: how much a more general architecture gives up against the
    per-target model on the same split.

    Deliberately reports ratios and stops. A general architecture does not
    need to win on absolute MAE — losing little while collapsing the data
    and maintenance cost may well be preferable, and that trade-off is a
    human decision (§12), not something this code should resolve.
    """

    reference_mae: float
    candidate_mae: float
    reference_spearman: float | None
    candidate_spearman: float | None

    @property
    def mae_ratio(self) -> float | None:
        """>1 means the candidate is worse. None when the reference is a
        perfect 0.0 and the ratio would be meaningless.
        """
        if self.reference_mae == 0.0:
            return None
        return self.candidate_mae / self.reference_mae

    @property
    def spearman_ratio(self) -> float | None:
        if not self.reference_spearman:
            return None
        return (self.candidate_spearman or 0.0) / self.reference_spearman
