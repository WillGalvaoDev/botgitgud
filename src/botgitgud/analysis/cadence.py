"""T0.6 — cooldown cadence and MAJOR/MINOR classification.

Fixes achado 3.3 (docs/relario.md): legacy/bot.py:465-474 derives
`avg_cd_duration` from the *instant of the first cast* whenever a spell has
only one observed usage — a single-use consumable item cast once at 200s
gets treated as a "200-second cooldown". A real long cooldown used once
(e.g. a 3-minute
major CD cast at 10s) gets misclassified MINOR and is then filtered out by
the eligibility check entirely (`avg_cd >= 18.0` in
`discover_clean_major_cds`), which is backwards: single-usage abilities are
disproportionately likely to be exactly the important long cooldowns.

This module never derives a cooldown from a single timestamp. With fewer
than two observed usages, `observed_interval_median` is `None` — full stop
— and classification falls back to usage-count heuristics instead.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

CdType = Literal["MAJOR", "MINOR"]

_MAJOR_THRESHOLD_S = 90.0
_MIN_ELIGIBLE_INTERVAL_S = 15.0
_MIN_ELIGIBLE_PRESENCE = 0.70
_SINGLE_USE_N_THRESHOLD = 1.5


@dataclass(frozen=True, slots=True)
class SpellCadence:
    observed_interval_median: float | None  # None when there are < 2 usages — never an instant
    observed_interval_iqr: float | None
    n_usages_median: float  # median usage count across the reference cohort
    base_cooldown: float | None  # from the spell catalog; None if unknown (always None in Fase 0)


def _interval_stats(usage_times: Sequence[float]) -> tuple[float | None, float | None]:
    if len(usage_times) < 2:
        return None, None

    intervals = [b - a for a, b in pairwise(usage_times)]
    median = statistics.median(intervals)

    if len(intervals) == 1:
        iqr = 0.0
    else:
        q1, _q2, q3 = statistics.quantiles(intervals, n=4, method="inclusive")
        iqr = q3 - q1

    return median, iqr


def compute_cadence(
    usage_times: Sequence[float],
    *,
    n_usages_median: float,
    base_cooldown: float | None = None,
) -> SpellCadence:
    """Build a SpellCadence from a sorted sequence of representative usage
    times (e.g. per-slot medians across a reference cohort, or a single
    player's own cast times). `usage_times` must be sorted ascending.
    """
    median, iqr = _interval_stats(usage_times)
    return SpellCadence(
        observed_interval_median=median,
        observed_interval_iqr=iqr,
        n_usages_median=n_usages_median,
        base_cooldown=base_cooldown,
    )


def classify_cd_type(cadence: SpellCadence) -> CdType:
    """MAJOR/MINOR classification, in strict order of evidence quality:
    a known base cooldown beats an observed interval, which beats a raw
    usage-count heuristic for single-usage abilities with no other data.
    """
    if cadence.base_cooldown is not None:
        return "MAJOR" if cadence.base_cooldown >= _MAJOR_THRESHOLD_S else "MINOR"

    if cadence.observed_interval_median is not None:
        return "MAJOR" if cadence.observed_interval_median >= _MAJOR_THRESHOLD_S else "MINOR"

    return "MAJOR" if cadence.n_usages_median <= _SINGLE_USE_N_THRESHOLD else "MINOR"


def is_eligible(
    spell_id: int,
    presence: float,
    cadence: SpellCadence,
    *,
    blacklist: frozenset[int],
) -> bool:
    """Eligibility filter, replacing legacy/bot.py's discover_clean_major_cds.

    The interval condition is deliberately permissive when unknown: a
    single-usage ability (interval median = None) is never excluded on that
    basis alone — the legacy filter did exactly that, discarding the
    longest, most important cooldowns just because they were used once.
    """
    if presence < _MIN_ELIGIBLE_PRESENCE:
        return False
    if cadence.base_cooldown is not None and cadence.base_cooldown < _MIN_ELIGIBLE_INTERVAL_S:
        return False
    if (
        cadence.observed_interval_median is not None
        and cadence.observed_interval_median < _MIN_ELIGIBLE_INTERVAL_S
    ):
        return False
    return spell_id not in blacklist
