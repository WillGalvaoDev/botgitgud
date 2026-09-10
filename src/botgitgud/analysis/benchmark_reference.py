"""Pure selection of an aspirational execution reference from a cohort."""

from __future__ import annotations

import math
from collections.abc import Sequence

from botgitgud.analysis.benchmark_aggregate import dedup_priority, player_identity
from botgitgud.domain.models import PlayerLog

REFERENCE_MIN_N = 8
REFERENCE_FLOOR_PERCENTILE = 60.0


def _dps(log: PlayerLog) -> float:
    return log.dps if log.dps is not None else 0.0


def _rank_key(log: PlayerLog) -> tuple[float, tuple[float, str, int], tuple[str, str]]:
    """Ascending DPS rank with the project's canonical stable log tie-break."""
    return (_dps(log), dedup_priority(log), player_identity(log))


def select_benchmark_reference(
    player_log: PlayerLog, cohort: Sequence[PlayerLog]
) -> list[PlayerLog]:
    """Return the conditional upper tail of ``cohort`` for ``player_log``.

    Ranks are empirical ranks inside the execution cohort, never WCL's global
    ``rankPercent``.  The player threshold is its insertion rank in that same
    distribution.  When the upper tail is thin, the largest of eight members
    or one third of the cohort is selected from the top.
    """
    if not cohort:
        raise ValueError("benchmark reference requires a non-empty cohort")

    ranked = sorted(cohort, key=_rank_key)
    n = len(ranked)
    player_rank = 100.0 * sum(_dps(member) <= _dps(player_log) for member in ranked) / n
    threshold = max(player_rank, REFERENCE_FLOOR_PERCENTILE)
    reference = [
        member for index, member in enumerate(ranked, start=1) if 100.0 * index / n >= threshold
    ]

    if len(reference) < REFERENCE_MIN_N:
        fallback_n = min(n, max(REFERENCE_MIN_N, math.ceil(n / 3)))
        reference = ranked[-fallback_n:]
    return reference
