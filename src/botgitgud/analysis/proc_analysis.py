"""Derived analysis of the player's self offensive auras.

Aura bands arrive in milliseconds while cast timestamps are seconds relative
to the fight start.  This module converts each clamped band to seconds once,
at the boundary, and performs every correlation in seconds thereafter.

Cast-dependent metrics deliberately concern *any* offensively classified
player cast.  The available data does not identify which spell, if any,
consumes an aura, so these metrics must not be interpreted as proof that a
specific proc was consumed correctly.

Band boundaries are inclusive: a cast at either the start or end belongs to
the band.  Durations remain ordinary interval lengths, so inclusivity does not
add time to overlap calculations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from botgitgud.analysis.ability_classification import classify_abilities
from botgitgud.domain.ability_role import OFFENSIVE_ROLES, AbilityRole
from botgitgud.domain.models import AuraBand, PlayerLog

ProcMetricStatus: TypeAlias = Literal["available", "unavailable"]

NO_AURA_DETAILS_REASON = "aura_details_unavailable"
_SELF_PROC_ROLES = frozenset({AbilityRole.SELF_OFFENSIVE_PROC, AbilityRole.SELF_OFFENSIVE_BUFF})


@dataclass(frozen=True, slots=True)
class ProcMetrics:
    spell_id: int
    role: AbilityRole
    status: ProcMetricStatus
    reasons: tuple[str, ...]
    uptime_frac: float | None
    procs: int | None
    procs_per_min: float | None
    time_to_consume_s: tuple[float, ...]
    alignment_frac: float | None
    overlap_s: float | None
    possibly_wasted_bands: int | None

    def __post_init__(self) -> None:
        if bool(self.reasons) == (self.status == "available"):
            raise ValueError("reasons must be empty exactly when status is available")
        if self.role not in _SELF_PROC_ROLES:
            raise ValueError("role must be a self offensive proc or buff")


@dataclass(frozen=True, slots=True)
class ProcAnalysis:
    status: ProcMetricStatus
    reasons: tuple[str, ...]
    metrics: tuple[ProcMetrics, ...]

    def __post_init__(self) -> None:
        if bool(self.reasons) == (self.status == "available"):
            raise ValueError("reasons must be empty exactly when status is available")


def analyze_procs(log: PlayerLog) -> ProcAnalysis:
    """Return deterministic, evidence-only metrics for self offensive auras."""
    if not log.aura_details:
        return ProcAnalysis("unavailable", (NO_AURA_DETAILS_REASON,), ())

    classifications = classify_abilities(log)
    offensive_casts_s = tuple(
        sorted(
            cast_s
            for spell_id, cast_times in log.cast_timeline.items()
            if (classification := classifications.get(spell_id)) is not None
            and classification.role in OFFENSIVE_ROLES
            for cast_s in cast_times
        )
    )

    metrics: list[ProcMetrics] = []
    for spell_id, detail in sorted(log.aura_details.items()):
        classification = classifications.get(spell_id)
        if classification is None or classification.role not in _SELF_PROC_ROLES:
            continue

        bands_s = _clamped_bands_s(detail.bands, log.fight.duration_s)
        casts_by_band = tuple(
            tuple(cast_s for cast_s in offensive_casts_s if start_s <= cast_s <= end_s)
            for start_s, end_s in bands_s
        )
        aligned_casts = sum(
            any(start_s <= cast_s <= end_s for start_s, end_s in bands_s)
            for cast_s in offensive_casts_s
        )
        duration_s = log.fight.duration_s
        metrics.append(
            ProcMetrics(
                spell_id=spell_id,
                role=classification.role,
                status="available",
                reasons=(),
                uptime_frac=log.uptimes.get(spell_id),
                procs=detail.total_uses,
                procs_per_min=(
                    detail.total_uses / (duration_s / 60.0) if duration_s > 0.0 else None
                ),
                time_to_consume_s=tuple(
                    casts[0] - start_s
                    for (start_s, _end_s), casts in zip(bands_s, casts_by_band, strict=True)
                    if casts
                ),
                alignment_frac=(
                    aligned_casts / len(offensive_casts_s) if offensive_casts_s else None
                ),
                overlap_s=_overlap_s(bands_s),
                possibly_wasted_bands=sum(not casts for casts in casts_by_band),
            )
        )

    return ProcAnalysis("available", (), tuple(metrics))


def _clamped_bands_s(
    bands: tuple[AuraBand, ...], duration_s: float
) -> tuple[tuple[float, float], ...]:
    """Clamp API millisecond bands, convert once to seconds, and sort."""
    duration_ms = max(duration_s, 0.0) * 1000.0
    return tuple(
        sorted(
            (
                max(0.0, min(float(band.start_ms), duration_ms)) / 1000.0,
                max(0.0, min(float(band.end_ms), duration_ms)) / 1000.0,
            )
            for band in bands
        )
    )


def _overlap_s(bands_s: tuple[tuple[float, float], ...]) -> float:
    """Measure the union of regions covered by at least two bands."""
    events: list[tuple[float, int]] = []
    for start_s, end_s in bands_s:
        events.append((start_s, 1))
        events.append((end_s, -1))

    overlap_s = 0.0
    active = 0
    previous_s: float | None = None
    for timestamp_s, delta in sorted(events, key=lambda event: (event[0], -event[1])):
        if previous_s is not None and active >= 2:
            overlap_s += timestamp_s - previous_s
        active += delta
        previous_s = timestamp_s
    return overlap_s
