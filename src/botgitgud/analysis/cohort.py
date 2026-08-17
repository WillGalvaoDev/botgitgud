"""T0.8 — cohort size thresholds and duration-band filtering/normalization.

See docs/desvios.md D-9: the literal grep target in the acceptance criteria
(src/botgitgud/ingest/rankings.py) is Phase 1 file layout (T1.3/T1.4) that
doesn't exist yet in Fase 0. This module holds the same logic and is wired
into bot.py, consistent with how T0.5-T0.7 built focused analysis/* modules
instead of building out the Fase-1 architecture early.

Corrects achados 3.9 (absolute ±30s duration filter) and 3.10/the missing
cohort-size guard: docs/schema_confirmado.md §8 measured that
characterRankings is a leaderboard with as few as ~26 entries for a real
encounter/spec/difficulty — not a population sample with hundreds of
candidates. Filtering to ±7-30s absolute, as legacy did, left as few as 2
usable references for the project's own fixture log.

Duration stops being a hard filter and becomes an adjustment covariate:
- SANITY_BAND_PCT (±35%) excludes only structurally different kills
  (wildly different phase counts, wipes mislabeled as kills, etc.) — it is
  not meant to narrow the cohort down to "similar" kills.
- POSITIONAL_BAND_PCT (±12%) is the narrower band used only for metrics
  that are inherently tied to *when* in the fight something happens (cast
  timing) or that scale with fight length (raw usage counts) — until T2.4
  brings real phase-based time normalization.
"""

from __future__ import annotations

from typing import Literal

SANITY_BAND_PCT = 0.35
POSITIONAL_BAND_PCT = 0.12
POSITIONAL_MIN_N = 8

COHORT_MIN_HARD = 8
COHORT_MIN_WARN = 20
COHORT_MAX = 100
MAX_RANKING_PAGES = 10

CohortSizeStatus = Literal["insufficient", "warn", "ok"]


def within_sanity_band(candidate_duration_s: float, target_duration_s: float) -> bool:
    """±35% band: excludes only structurally different kills."""
    if target_duration_s <= 0:
        return False
    return abs(candidate_duration_s - target_duration_s) / target_duration_s <= SANITY_BAND_PCT


def within_positional_band(candidate_duration_s: float, target_duration_s: float) -> bool:
    """±12% band: for cast-timing/positional metrics only (pre-T2.4)."""
    if target_duration_s <= 0:
        return False
    return abs(candidate_duration_s - target_duration_s) / target_duration_s <= POSITIONAL_BAND_PCT


def classify_cohort_size(n: int) -> CohortSizeStatus:
    if n < COHORT_MIN_HARD:
        return "insufficient"
    if n < COHORT_MIN_WARN:
        return "warn"
    return "ok"


def usage_rate_per_minute(usage_count: int, duration_s: float) -> float:
    """A raw cast count scales with fight length (T0.8's normalization
    table) — comparing counts directly across reference players with
    different durations is misleading. Converting to a per-minute rate
    makes players with different (but positional-band-similar) durations
    comparable.
    """
    if duration_s <= 0:
        return 0.0
    return usage_count / (duration_s / 60.0)


def usage_count_at_duration(rate_per_minute: float, duration_s: float) -> float:
    """Inverse of usage_rate_per_minute: rescale a cohort's median rate back
    into an absolute expected count at a specific (e.g. the analyzed
    player's own) duration — preserves the raw-count contract T0.6's
    classify_cd_type/is_eligible were calibrated and tested against.
    """
    return rate_per_minute * (duration_s / 60.0)
