"""T2.1 — multivariate cohort matching with ordered covariate degradation
(docs/implementacao.md T2.1, achado 3.4).

encounter_id/difficulty/partition/class_name/spec_name are exact-matched
already by ingest/rankings.py's characterRankings query (never checked
here) — this module only filters the ALREADY-fetched candidate PlayerLogs
on the remaining covariates: duration_s, item_level, tier_pieces,
has_augmentation, external_buffs, talent_cluster.

docs/desvios.md D-24 (resolved by T2.2): `talent_cluster` now uses
analysis/talent_cluster.py's Jaccard similarity — a candidate matches
"strictly" when its build is >= JACCARD_THRESHOLD similar to the target's,
same pairwise-to-target shape as item_level/tier_pieces (this is a
pairwise-to-target check, never a full cohort-wide clustering call — EC.4
removed the report-level clustering that used to live in analysis/
talent_cluster.py; see that module's docstring for why).

EC.3/M4: `matching_policy_version` (same string as `domain/models.py`'s
`CohortCriteria.matching_policy_version`, EC.2) selects which covariate
set `match_cohort` uses. `"v1"` remains available unchanged. The default
`"v2"` removes `talent_cluster` from inclusion ENTIRELY — never tried
strict, never something to relax, simply not a matching covariate — so
the execution cohort becomes independent of the player's setup, matching
the architectural principle that setup and execution are separate
analyses (Encounter Benchmark, milestone SA, already established this;
EC.3 makes the Execution Cohort side of that boundary real). Spell-
specific feature compatibility (EC.1's `n_with_spell`/uptime-distribution
fix) is untouched by this — it already operates on whatever `matched_logs`
`match_cohort` returns, regardless of which covariates produced it.

`analysis/pipeline.py` passes the criteria policy through, so v1 and v2
pools remain independently addressable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from botgitgud.analysis.benchmark_aggregate import dedup_priority, player_identity
from botgitgud.analysis.cohort import COHORT_MIN_HARD, COHORT_STRETCH_N, COHORT_TARGET_N
from botgitgud.analysis.talent_cluster import JACCARD_THRESHOLD, jaccard_similarity
from botgitgud.domain.external_buffs import EXTERNAL_OFFENSIVE_IDS
from botgitgud.domain.models import DEFAULT_MATCHING_POLICY_VERSION, PlayerLog

DEGRADATION_ORDER: tuple[str, ...] = (
    "tier_pieces",
    "external_buffs",
    "item_level",
    "talent_cluster",
    "has_augmentation",
    "duration",
)

# EC.3/M4: setup and augmentation are adjustment data under v2, never
# inclusion covariates to relax.
DEGRADATION_ORDER_V2: tuple[str, ...] = tuple(
    c for c in DEGRADATION_ORDER if c not in {"talent_cluster", "has_augmentation"}
)

ITEM_LEVEL_BAND = 5.0
TIER_PIECES_BAND = 1
DURATION_BANDS_PCT: tuple[float, ...] = (0.07, 0.12, 0.20)
DURATION_FLOOR_S = 15.0

_ALL_COVARIATES = (
    "tier_pieces",
    "external_buffs",
    "item_level",
    "talent_cluster",
    "has_augmentation",
)

# EC.3/M4: v2's execution cohort is independent of setup, while augmentation
# is declared for adjustment rather than used as a binary inclusion filter.
_ALL_COVARIATES_V2 = tuple(
    c for c in _ALL_COVARIATES if c not in {"talent_cluster", "has_augmentation"}
)


@dataclass(frozen=True, slots=True)
class MatchReport:
    matched: tuple[str, ...]
    relaxed: tuple[str, ...]
    n_members: int
    excluded_self: int = 0
    excluded_non_kill: int = 0
    deduped_pull: int = 0
    deduped_player: int = 0
    adjustment_covariates: tuple[str, ...] = ()
    cohort_level: Literal["HARD", "TARGET", "STRETCH"] = "HARD"


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


def _same_talent_cluster(
    candidate: frozenset[tuple[int, int]], target: frozenset[tuple[int, int]]
) -> bool:
    if not candidate or not target:
        return False
    return jaccard_similarity(candidate, target) >= JACCARD_THRESHOLD


def match_cohort(
    target: PlayerLog,
    candidates: Sequence[PlayerLog],
    *,
    min_n: int = COHORT_TARGET_N,
    matching_policy_version: str = DEFAULT_MATCHING_POLICY_VERSION,
) -> tuple[list[PlayerLog], MatchReport]:
    """Filters `candidates` by every covariate at its strictest setting;
    if fewer than `min_n` survive, relaxes covariates one at a time in
    degradation order (duration widens through DURATION_BANDS_PCT instead
    of being dropped outright) until `min_n` is reached or every relaxable
    covariate is exhausted. Never touches encounter/difficulty/partition/
    class/spec — those are exact by construction (pre-filtered upstream).

    `matching_policy_version` (EC.3/M4): `"v1"` uses every
    covariate in `_ALL_COVARIATES`/`DEGRADATION_ORDER`, `talent_cluster`
    included, exactly as before this parameter existed. Any other value
    (`"v2"`, the default) uses `_ALL_COVARIATES_V2`/`DEGRADATION_ORDER_V2`:
    `talent_cluster` and `has_augmentation` are never added to `active` and
    therefore never become filters or relaxable covariates.

    May still return fewer than `min_n` members after every relaxation —
    callers check that against COHORT_MIN_HARD themselves (analysis/
    cohort.py's classify_cohort_size), same as the pre-T2.1 pipeline.
    """
    target_identity = player_identity(target)
    without_self = [c for c in candidates if player_identity(c) != target_identity]
    excluded_self = len(candidates) - len(without_self)

    kills = [c for c in without_self if c.fight.kill]
    excluded_non_kill = len(without_self) - len(kills)

    by_pull: dict[tuple[str, int], list[PlayerLog]] = {}
    for candidate in kills:
        pull = (candidate.fight.report_code, candidate.fight.fight_id)
        by_pull.setdefault(pull, []).append(candidate)
    one_per_pull = [
        min(by_pull[pull], key=lambda log: (dedup_priority(log), player_identity(log)))
        for pull in sorted(by_pull)
    ]
    deduped_pull = len(kills) - len(one_per_pull)

    by_player: dict[tuple[str, str], list[PlayerLog]] = {}
    for candidate in one_per_pull:
        by_player.setdefault(player_identity(candidate), []).append(candidate)
    hygienic_candidates = [
        min(by_player[identity], key=dedup_priority) for identity in sorted(by_player)
    ]
    deduped_player = len(one_per_pull) - len(hygienic_candidates)

    is_v1 = matching_policy_version == "v1"
    all_covariates = _ALL_COVARIATES if is_v1 else _ALL_COVARIATES_V2
    degradation_order = DEGRADATION_ORDER if is_v1 else DEGRADATION_ORDER_V2

    active: set[str] = set(all_covariates)
    relaxed: list[str] = []
    duration_band_idx = 0

    def _apply() -> list[PlayerLog]:
        pct = DURATION_BANDS_PCT[duration_band_idx]
        result = []
        for c in hygienic_candidates:
            if not _within_duration_band(c.fight.duration_s, target.fight.duration_s, pct):
                continue
            if "tier_pieces" in active and not _within_tier_pieces_band(
                c.build.tier_pieces, target.build.tier_pieces
            ):
                continue
            if "external_buffs" in active and (
                c.build.external_buffs & EXTERNAL_OFFENSIVE_IDS
                != target.build.external_buffs & EXTERNAL_OFFENSIVE_IDS
            ):
                continue
            if "item_level" in active and not _within_item_level_band(
                c.build.item_level, target.build.item_level
            ):
                continue
            if "talent_cluster" in active and not _same_talent_cluster(
                c.build.talent_pairs, target.build.talent_pairs
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
    # Custom callers retain the established single-target cascade.  The
    # canonical M10 cascade first earns the viability floor; only until that
    # point may offensive context be relaxed (RB-8).
    first_target = COHORT_MIN_HARD if min_n == COHORT_TARGET_N else min_n
    remaining_covariates: list[str] = []
    for position, covariate in enumerate(degradation_order):
        if len(filtered) >= first_target:
            remaining_covariates = list(degradation_order[position:])
            break
        if covariate == "duration":
            while len(filtered) < first_target and duration_band_idx < len(DURATION_BANDS_PCT) - 1:
                duration_band_idx += 1
                filtered = _apply()
            continue
        active.discard(covariate)
        relaxed.append(covariate)
        filtered = _apply()
    else:
        remaining_covariates = []

    # After HARD, growth toward TARGET and STRETCH may spend only measured
    # low-bias covariates.  In particular, external buffs, augmentation, and
    # duration stay matched even when that means honestly stopping at HARD.
    if min_n == COHORT_TARGET_N and len(filtered) >= COHORT_MIN_HARD:
        low_bias = {"tier_pieces", "item_level", "talent_cluster"}
        for covariate in remaining_covariates:
            if len(filtered) >= COHORT_STRETCH_N:
                break
            if covariate not in low_bias:
                continue
            active.discard(covariate)
            relaxed.append(covariate)
            filtered = _apply()

    cohort_level: Literal["HARD", "TARGET", "STRETCH"] = "HARD"
    if len(filtered) >= COHORT_STRETCH_N:
        cohort_level = "STRETCH"
    elif len(filtered) >= COHORT_TARGET_N:
        cohort_level = "TARGET"

    matched = tuple(c for c in all_covariates if c in active)
    duration_pct = DURATION_BANDS_PCT[duration_band_idx]
    matched = (*matched, f"duration±{int(duration_pct * 100)}%")
    return filtered, MatchReport(
        matched=matched,
        relaxed=tuple(relaxed),
        n_members=len(filtered),
        excluded_self=excluded_self,
        excluded_non_kill=excluded_non_kill,
        deduped_pull=deduped_pull,
        deduped_player=deduped_player,
        adjustment_covariates=() if is_v1 else ("has_augmentation",),
        cohort_level=cohort_level,
    )
