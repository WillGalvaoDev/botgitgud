"""T2.1 — multivariate cohort matching with ordered covariate degradation
(docs/implementacao.md T2.1, achado 3.4).

encounter_id/difficulty/partition/class_name/spec_name are exact-matched
already by ingest/rankings.py's characterRankings query (never checked
here) — this module only filters the ALREADY-fetched candidate PlayerLogs
on the remaining covariates: duration_s, item_level, tier_pieces,
has_augmentation, external_buffs, talent_cluster.

docs/desvios.md D-24: `talent_cluster` needs T2.2's Jaccard clustering,
which doesn't exist yet — it's always pre-relaxed (never attempted) until
T2.2 wires real cluster assignment in here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from botgitgud.domain.models import PlayerLog

DEGRADATION_ORDER: tuple[str, ...] = (
    "tier_pieces",
    "external_buffs",
    "item_level",
    "talent_cluster",
    "has_augmentation",
    "duration",
)

ITEM_LEVEL_BAND = 5.0
TIER_PIECES_BAND = 1
DURATION_BANDS_PCT: tuple[float, ...] = (0.07, 0.12, 0.20)
DURATION_FLOOR_S = 15.0

_ALL_COVARIATES = ("tier_pieces", "external_buffs", "item_level", "has_augmentation")


@dataclass(frozen=True, slots=True)
class MatchReport:
    matched: tuple[str, ...]
    relaxed: tuple[str, ...]
    n_members: int


def _within_duration_band(candidate_s: float, target_s: float, pct: float) -> bool:
    tolerance = max(target_s * pct, DURATION_FLOOR_S)
    return abs(candidate_s - target_s) <= tolerance


def _within_item_level_band(candidate: float | None, target: float | None) -> bool:
    if candidate is None or target is None:
        return False
    return abs(candidate - target) <= ITEM_LEVEL_BAND


def _within_tier_pieces_band(candidate: int | None, target: int | None) -> bool:
    if candidate is None or target is None:
        return False
    return abs(candidate - target) <= TIER_PIECES_BAND


def match_cohort(
    target: PlayerLog, candidates: Sequence[PlayerLog], *, min_n: int
) -> tuple[list[PlayerLog], MatchReport]:
    """Filters `candidates` by every covariate at its strictest setting;
    if fewer than `min_n` survive, relaxes covariates one at a time in
    DEGRADATION_ORDER (duration widens through DURATION_BANDS_PCT instead
    of being dropped outright) until `min_n` is reached or every relaxable
    covariate is exhausted. Never touches encounter/difficulty/partition/
    class/spec — those are exact by construction (pre-filtered upstream).

    May still return fewer than `min_n` members after every relaxation —
    callers check that against COHORT_MIN_HARD themselves (analysis/
    cohort.py's classify_cohort_size), same as the pre-T2.1 pipeline.
    """
    active: set[str] = set(_ALL_COVARIATES)
    relaxed: list[str] = ["talent_cluster"]  # D-24: never attempted yet
    duration_band_idx = 0

    def _apply() -> list[PlayerLog]:
        pct = DURATION_BANDS_PCT[duration_band_idx]
        result = []
        for c in candidates:
            if not _within_duration_band(c.fight.duration_s, target.fight.duration_s, pct):
                continue
            if "tier_pieces" in active and not _within_tier_pieces_band(
                c.build.tier_pieces, target.build.tier_pieces
            ):
                continue
            if "external_buffs" in active and c.build.external_buffs != target.build.external_buffs:
                continue
            if "item_level" in active and not _within_item_level_band(
                c.build.item_level, target.build.item_level
            ):
                continue
            if (
                "has_augmentation" in active
                and c.build.has_augmentation != target.build.has_augmentation
            ):
                continue
            result.append(c)
        return result

    filtered = _apply()
    for covariate in DEGRADATION_ORDER:
        if len(filtered) >= min_n:
            break
        if covariate in relaxed:
            continue
        if covariate == "duration":
            while len(filtered) < min_n and duration_band_idx < len(DURATION_BANDS_PCT) - 1:
                duration_band_idx += 1
                filtered = _apply()
            continue
        active.discard(covariate)
        relaxed.append(covariate)
        filtered = _apply()

    matched = tuple(c for c in _ALL_COVARIATES if c in active)
    duration_pct = DURATION_BANDS_PCT[duration_band_idx]
    matched = (*matched, f"duration±{int(duration_pct * 100)}%")
    return filtered, MatchReport(matched=matched, relaxed=tuple(relaxed), n_members=len(filtered))
