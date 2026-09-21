"""T2.3 — quantile-based grading, bootstrap CI, and Benjamini-Hochberg
multiple-comparison control (docs/implementacao.md T2.3, corrige achados
3.6/3.7 e o `stdev` morto).

Replaces the fixed 10s/25s thresholds (report/text.py's old
`_match_status`) with a grade relative to the reference cohort's own
empirical distribution at that position — "3s de desvio num CD de 30s e
20s num CD de 3min recebem tratamento proporcional."
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

Grade = Literal["green", "yellow", "red", "insufficient"]

MIN_N_FOR_GRADING = 15  # below this, never color — "melhor não opinar que opinar errado"
N_BOOTSTRAP = 2000
BOOTSTRAP_SEED = 20260817
CI_LEVEL = 0.90
FDR = 0.10


@dataclass(frozen=True, slots=True)
class QuantileStats:
    n: int
    p10: float | None
    p25: float | None
    p50: float | None
    p75: float | None
    p90: float | None


def compute_quantile_stats(times: Sequence[float]) -> QuantileStats:
    n = len(times)
    if n == 0:
        return QuantileStats(n=0, p10=None, p25=None, p50=None, p75=None, p90=None)
    if n == 1:
        v = times[0]
        return QuantileStats(n=1, p10=v, p25=v, p50=v, p75=v, p90=v)
    # statistics.quantiles(data, n=100) -> 99 cut points; qs[k-1] is the k-th percentile.
    qs = statistics.quantiles(sorted(times), n=100, method="inclusive")
    return QuantileStats(n=n, p10=qs[9], p25=qs[24], p50=qs[49], p75=qs[74], p90=qs[89])


def empirical_quantile(value: float, reference_times: Sequence[float]) -> float | None:
    """Where `value` falls within `reference_times`' empirical CDF, in
    [0, 1] — ties split at the midpoint (standard mid-rank convention).
    None when there's no reference distribution to compare against.
    """
    n = len(reference_times)
    if n == 0:
        return None
    less = sum(1 for t in reference_times if t < value)
    equal = sum(1 for t in reference_times if t == value)
    return (less + 0.5 * equal) / n


def grade_from_quantile(q: float) -> Grade:
    if 0.25 <= q <= 0.75:
        return "green"
    if 0.10 <= q < 0.25 or 0.75 < q <= 0.90:
        return "yellow"
    return "red"


def grade_deviation(user_time: float, reference_times: Sequence[float]) -> Grade:
    """T2.3 acceptance: n < MIN_N_FOR_GRADING for this specific position
    never colors — "insufficient", never red just because the sample is
    thin.
    """
    if len(reference_times) < MIN_N_FOR_GRADING:
        return "insufficient"
    q = empirical_quantile(user_time, reference_times)
    assert q is not None  # len already checked above
    return grade_from_quantile(q)


def bootstrap_median_ci(
    times: Sequence[float],
    *,
    n_bootstrap: int = N_BOOTSTRAP,
    seed: int = BOOTSTRAP_SEED,
    ci_level: float = CI_LEVEL,
) -> tuple[float, float] | None:
    """90% CI for the reference median via resampling — `seed` is fixed
    (a local `random.Random`, never the global module state) so results
    are reproducible run to run without polluting anything else's RNG.
    """
    n = len(times)
    if n == 0:
        return None
    if n == 1:
        return times[0], times[0]

    rng = random.Random(seed)
    medians = sorted(
        statistics.median(times[rng.randrange(n)] for _ in range(n)) for _ in range(n_bootstrap)
    )
    tail = (1.0 - ci_level) / 2.0
    lo_idx = int(tail * n_bootstrap)
    hi_idx = min(int((1.0 - tail) * n_bootstrap), n_bootstrap - 1)
    return medians[lo_idx], medians[hi_idx]


def two_tailed_p_value(q: float) -> float:
    """A deviation's empirical quantile, turned into a two-tailed p-value
    for Benjamini-Hochberg: q=0.5 (exactly typical) -> p=1.0; q near 0 or
    1 (extreme in either direction) -> p near 0.0.
    """
    return 2 * min(q, 1 - q)


def benjamini_hochberg(p_values: Sequence[float], *, fdr: float = FDR) -> list[bool]:
    """Standard BH step-up procedure. Returns one bool per input p-value,
    same order — True means "survives the FDR control" (report as a real
    finding), False means "collapse into the non-significant section".
    """
    m = len(p_values)
    if m == 0:
        return []

    order = sorted(range(m), key=lambda i: p_values[i])
    threshold_rank = 0
    for rank, idx in enumerate(order, start=1):
        if p_values[idx] <= (rank / m) * fdr:
            threshold_rank = rank

    survives = [False] * m
    for idx in order[:threshold_rank]:
        survives[idx] = True
    return survives
