"""T2.4 — phase-cycle-aware interval derivation (docs/implementacao.md
T2.4, corrige achado 3.2, rotação-fantasma).

docs/schema_confirmado.md §7 (verified live against the Zarad fixture):
WCL's `fights[].phaseTransitions[].id` is the phase's IDENTIFIER, not a
sequential index — phases repeat in a cycle (this fight's sequence is
1, 2, 1, 2, 1). Keying anything by `phase_id` alone would silently merge
the 1st and 3rd occurrence of phase 1, recreating exactly the
"rotação-fantasma" this task exists to eliminate. Every interval is
identified by `(phase_id, occurrence)` instead.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from botgitgud.domain.models import PhaseInterval

FALLBACK_PHASE_ID = 0


def derive_phase_intervals(
    phase_transitions: Sequence[dict[str, Any]],
    *,
    fight_start_ms: float,
    fight_end_ms: float,
) -> tuple[PhaseInterval, ...]:
    """`phase_transitions` is the raw `fights[].phaseTransitions` list
    (each `{"id": int, "startTime": float}`), in WCL's own chronological
    order. Falls back to a single interval spanning the whole fight
    (`phase_id=FALLBACK_PHASE_ID`) when there's no phase data —
    "encontros sem fases declaradas usam uma única 'fase 0' abrangendo a
    luta inteira. O comportamento degrada para o da Fase 0 sem quebrar."
    """
    if not phase_transitions:
        return (PhaseInterval(FALLBACK_PHASE_ID, 0, fight_start_ms, fight_end_ms),)

    occurrence_counts: dict[int, int] = {}
    intervals: list[PhaseInterval] = []
    for i, t in enumerate(phase_transitions):
        phase_id = int(t["id"])
        occurrence = occurrence_counts.get(phase_id, 0)
        occurrence_counts[phase_id] = occurrence + 1
        start_ms = float(t["startTime"])
        end_ms = (
            float(phase_transitions[i + 1]["startTime"])
            if i + 1 < len(phase_transitions)
            else fight_end_ms
        )
        intervals.append(PhaseInterval(phase_id, occurrence, start_ms, end_ms))
    return tuple(intervals)


def find_interval(intervals: Sequence[PhaseInterval], timestamp_ms: float) -> PhaseInterval | None:
    """The interval containing `timestamp_ms` — half-open on `[start,
    end)`, except the very last interval, which is closed on `end` too
    (a cast landing exactly on the fight's own end timestamp still needs
    a home). None only if `timestamp_ms` falls outside every interval
    (e.g. a malformed event timestamp before the fight even starts).
    """
    for idx, interval in enumerate(intervals):
        is_last = idx == len(intervals) - 1
        if interval.start_ms <= timestamp_ms < interval.end_ms:
            return interval
        if is_last and timestamp_ms == interval.end_ms:
            return interval
    return None
