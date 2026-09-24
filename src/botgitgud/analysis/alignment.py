"""T0.5 — monotonic sequence alignment between a player's cast timeline and
a reference timeline.

Corrects achado 3.1 of the audit of the original bot, its most severe finding:
the original nearest-neighbor matcher always mapped
each player cast to whichever reference time is closest, which can never
report a missed cooldown usage and is systematically biased toward "green".

This module replaces that with global sequence alignment (Needleman-Wunsch
with a continuous match cost), which explicitly distinguishes:
- MATCH:  a player cast paired with an expected usage,
- MISSED: an expected usage with no corresponding player cast,
- EXTRA:  a player cast with no corresponding expected usage.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class AlignmentKind(StrEnum):
    MATCH = "match"
    MISSED = "missed"
    EXTRA = "extra"


@dataclass(frozen=True, slots=True)
class AlignmentStep:
    kind: AlignmentKind
    user_index: int | None  # index into user_times; None iff MISSED
    ref_index: int | None  # index into ref_times; None iff EXTRA
    user_time: float | None
    ref_time: float | None
    delta: float | None  # user_time - ref_time; only set on MATCH


@dataclass(frozen=True, slots=True)
class Alignment:
    steps: tuple[AlignmentStep, ...]
    total_cost: float
    n_matched: int
    n_missed: int
    n_extra: int


def _require_sorted(values: Sequence[float], name: str) -> None:
    if list(values) != sorted(values):
        msg = f"{name} deve estar ordenado de forma crescente; recebido: {list(values)!r}"
        raise ValueError(msg)


def align(
    user_times: Sequence[float],
    ref_times: Sequence[float],
    *,
    gap_penalty: float = 25.0,  # mirrors Settings.gap_penalty_s's own default; see comparison.py
) -> Alignment:
    """Align `user_times` against `ref_times` via global DP alignment.

    Tie-break precedence when two or more operations cost the same at a
    given cell: MATCH > MISSED > EXTRA (checked in that order below, and the
    strict `<` comparison means the first-checked candidate wins any tie —
    this determinism is a documented, tested property, not an accident).
    """
    _require_sorted(user_times, "user_times")
    _require_sorted(ref_times, "ref_times")

    m, n = len(user_times), len(ref_times)

    # D[i][j]: cheapest cost to align user_times[:i] against ref_times[:j].
    # choice[i][j]: which operation achieved D[i][j] ("match"/"missed"/"extra"),
    # unused (None) for the (0, 0) base cell.
    d: list[list[float]] = [[0.0] * (n + 1) for _ in range(m + 1)]
    choice: list[list[AlignmentKind | None]] = [[None] * (n + 1) for _ in range(m + 1)]

    for i in range(1, m + 1):
        d[i][0] = i * gap_penalty
        choice[i][0] = AlignmentKind.EXTRA
    for j in range(1, n + 1):
        d[0][j] = j * gap_penalty
        choice[0][j] = AlignmentKind.MISSED

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            match_cost = d[i - 1][j - 1] + abs(user_times[i - 1] - ref_times[j - 1])
            missed_cost = d[i][j - 1] + gap_penalty
            extra_cost = d[i - 1][j] + gap_penalty

            best_cost, best_kind = match_cost, AlignmentKind.MATCH
            if missed_cost < best_cost:
                best_cost, best_kind = missed_cost, AlignmentKind.MISSED
            if extra_cost < best_cost:
                best_cost, best_kind = extra_cost, AlignmentKind.EXTRA

            d[i][j] = best_cost
            choice[i][j] = best_kind

    steps_reversed: list[AlignmentStep] = []
    i, j = m, n
    while i > 0 or j > 0:
        kind = choice[i][j]
        if kind is AlignmentKind.MATCH:
            i, j_prev = i - 1, j - 1
            u_t, r_t = user_times[i], ref_times[j_prev]
            steps_reversed.append(
                AlignmentStep(
                    kind=AlignmentKind.MATCH,
                    user_index=i,
                    ref_index=j_prev,
                    user_time=u_t,
                    ref_time=r_t,
                    delta=u_t - r_t,
                )
            )
            j = j_prev
        elif kind is AlignmentKind.MISSED:
            j -= 1
            steps_reversed.append(
                AlignmentStep(
                    kind=AlignmentKind.MISSED,
                    user_index=None,
                    ref_index=j,
                    user_time=None,
                    ref_time=ref_times[j],
                    delta=None,
                )
            )
        else:  # EXTRA
            i -= 1
            steps_reversed.append(
                AlignmentStep(
                    kind=AlignmentKind.EXTRA,
                    user_index=i,
                    ref_index=None,
                    user_time=user_times[i],
                    ref_time=None,
                    delta=None,
                )
            )

    steps = tuple(reversed(steps_reversed))
    n_matched = sum(1 for s in steps if s.kind is AlignmentKind.MATCH)
    n_missed = sum(1 for s in steps if s.kind is AlignmentKind.MISSED)
    n_extra = sum(1 for s in steps if s.kind is AlignmentKind.EXTRA)

    return Alignment(
        steps=steps,
        total_cost=d[m][n],
        n_matched=n_matched,
        n_missed=n_missed,
        n_extra=n_extra,
    )
