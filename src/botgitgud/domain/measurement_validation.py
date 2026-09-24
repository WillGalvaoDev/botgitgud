"""Shared validation of the measurement interval and observed player casts."""

import math

from botgitgud.domain.models import CollectionProvenance, PlayerLog


def collection_interval_problem(collection: CollectionProvenance, duration_s: float) -> str | None:
    start, end = collection.requested_start_ms, collection.requested_end_ms
    if start is None or end is None:
        return "COLLECTION_INTERVAL_MISSING"
    if (
        not math.isfinite(duration_s)
        or duration_s <= 0
        or isinstance(start, bool)
        or isinstance(end, bool)
        or not math.isfinite(start)
        or not math.isfinite(end)
        or start < 0
        or end <= start
    ):
        return "INVALID_COLLECTION_INTERVAL"
    if not math.isclose((end - start) / 1000, duration_s, rel_tol=1e-12, abs_tol=1e-9):
        return "COLLECTION_INTERVAL_MISMATCH"
    return None


def valid_player_casts(log: PlayerLog, spell_id: int) -> bool:
    times = log.cast_timeline.get(spell_id, ())
    duration = log.fight.duration_s
    return (
        bool(times)
        and math.isfinite(duration)
        and duration > 0
        and all(not isinstance(t, bool) and math.isfinite(t) and 0 <= t <= duration for t in times)
    )
