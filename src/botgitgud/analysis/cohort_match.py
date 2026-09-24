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
from dataclasses import dataclass, replace
from typing import Literal

from botgitgud.analysis.benchmark_aggregate import dedup_priority, player_identity
from botgitgud.analysis.cohort import COHORT_MIN_HARD, COHORT_STRETCH_N, COHORT_TARGET_N
from botgitgud.analysis.measurement import damage_reference_id
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


@dataclass(frozen=True, slots=True)
class HygieneReport:
    """M2.3 §4.1: the identity/attempt-state hygiene step of the original
    `match_cohort`, extracted as its own stage so it can run once, before
    both M2.1 (which needs the hygienic set, never a covariate-filtered
    one) and the covariate ladder (`match_covariates`).
    """

    n_input: int
    n_output: int
    excluded_self: int
    excluded_non_kill: int
    deduped_pull: int
    deduped_player: int


@dataclass(frozen=True, slots=True)
class DuplicateConflictReport:
    """M2.3 §4.0 (D-M23-07): report of `quarantine_conflicting_duplicates`."""

    n_input: int
    n_output: int
    excluded_logs: int
    conflicting_ids: tuple[str, ...]


def quarantine_conflicting_duplicates(
    target: PlayerLog, candidates: Sequence[PlayerLog]
) -> tuple[list[PlayerLog], DuplicateConflictReport]:
    """M2.3 v003 §4.0 (D-M23-07): puts in quarantine every OBSERVATION —
    same player, same pull, `(report_code, fight_id, player_identity)`,
    never `dedup_priority` — whose representations disagree by value
    (`!=`), and closes the exclusion by `damage_reference_id`: every
    domain log sharing an id with a conflicting observation is removed
    too, not just the instances grouped together. `reference_id` already
    encodes `report_code:fight_id`, so this only ever reaches across
    `player_identity` within the SAME pull — e.g. the same character name
    on two different servers, which `player_identity` tells apart but
    `reference_id` does not (v002's re-review, R3: grouping by the hygiene
    tie key instead of the observation let a third representation with a
    different `dedup_priority` escape the group and win the pull, taking
    its id past this quarantine while its own duplicates were removed).

    Runs BEFORE `hygienic_candidates`, on the whole fetched list;
    `match_cohort`/`hygienic_candidates` never call this and are therefore
    untouched by it (SPEC §8 invariant 2). An observation whose
    representations are all equal by `==` is not a conflict and passes
    through untouched — the hygiene `min()` already collapses it to the
    same value regardless of which element it picks.

    Only self-referencing and non-kill logs are outside the domain; they
    are excluded later by `hygienic_candidates`'s own counters, never
    double-counted here. Conflict membership depends only on the multiset
    of candidates, never on their order, so a fixed input always
    quarantines the same ids under any permutation (SPEC §8 invariant 6).
    """
    target_identity = player_identity(target)

    def in_domain(log: PlayerLog) -> bool:
        return player_identity(log) != target_identity and log.fight.kill

    def observation_key(log: PlayerLog) -> tuple[str, int, tuple[str, str]]:
        return (log.fight.report_code, log.fight.fight_id, player_identity(log))

    groups: dict[tuple[str, int, tuple[str, str]], list[PlayerLog]] = {}
    for candidate in candidates:
        if in_domain(candidate):
            groups.setdefault(observation_key(candidate), []).append(candidate)

    conflicting_ids: set[str] = set()
    for group in groups.values():
        first = group[0]
        if any(log != first for log in group):
            conflicting_ids.update(damage_reference_id(log) for log in group)

    output = [
        c for c in candidates if not (in_domain(c) and damage_reference_id(c) in conflicting_ids)
    ]
    return output, DuplicateConflictReport(
        n_input=len(candidates),
        n_output=len(output),
        excluded_logs=len(candidates) - len(output),
        conflicting_ids=tuple(sorted(conflicting_ids)),
    )


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


def hygienic_candidates(
    target: PlayerLog, candidates: Sequence[PlayerLog]
) -> tuple[list[PlayerLog], HygieneReport]:
    """M2.3 §4.1: identity/attempt-state hygiene, split out of the original
    `match_cohort` so it can run exactly once and feed both M2.1 (the
    hygienic set, never covariate-filtered) and `match_covariates` (the
    ledger). Behaviour is byte-identical to what `match_cohort` always did
    for this stage: exclude self by `player_identity`, exclude non-kills,
    one reference per pull (`dedup_priority`), one per player.
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
    hygienic = [min(by_player[identity], key=dedup_priority) for identity in sorted(by_player)]
    deduped_player = len(one_per_pull) - len(hygienic)

    return hygienic, HygieneReport(
        n_input=len(candidates),
        n_output=len(hygienic),
        excluded_self=excluded_self,
        excluded_non_kill=excluded_non_kill,
        deduped_pull=deduped_pull,
        deduped_player=deduped_player,
    )


def match_covariates(
    target: PlayerLog,
    hygienic: Sequence[PlayerLog],
    *,
    min_n: int = COHORT_TARGET_N,
    matching_policy_version: str = DEFAULT_MATCHING_POLICY_VERSION,
) -> tuple[list[PlayerLog], MatchReport]:
    """M2.3 §4.1: the covariate-degradation half of the original
    `match_cohort`, taking an already-hygienic candidate list (see
    `hygienic_candidates`) instead of computing hygiene itself. Filters
    `hygienic` by every covariate at its strictest setting; if fewer than
    `min_n` survive, relaxes covariates one at a time in degradation order
    (duration widens through DURATION_BANDS_PCT instead of being dropped
    outright) until `min_n` is reached or every relaxable covariate is
    exhausted. Never touches encounter/difficulty/partition/class/spec —
    those are exact by construction (pre-filtered upstream).

    `matching_policy_version` (EC.3/M4): `"v1"` uses every
    covariate in `_ALL_COVARIATES`/`DEGRADATION_ORDER`, `talent_cluster`
    included, exactly as before this parameter existed. Any other value
    (`"v2"`, the default) uses `_ALL_COVARIATES_V2`/`DEGRADATION_ORDER_V2`:
    `talent_cluster` and `has_augmentation` are never added to `active` and
    therefore never become filters or relaxable covariates.

    May still return fewer than `min_n` members after every relaxation —
    callers check that against COHORT_MIN_HARD themselves (analysis/
    cohort.py's classify_cohort_size), same as the pre-T2.1 pipeline. The
    returned `MatchReport`'s hygiene counters (`excluded_self`,
    `excluded_non_kill`, `deduped_pull`, `deduped_player`) are always zero
    here — this function never sees the pre-hygiene candidates; `match_cohort`
    below fills them in from the `HygieneReport` it computed separately.
    """
    is_v1 = matching_policy_version == "v1"
    all_covariates = _ALL_COVARIATES if is_v1 else _ALL_COVARIATES_V2
    degradation_order = DEGRADATION_ORDER if is_v1 else DEGRADATION_ORDER_V2

    active: set[str] = set(all_covariates)
    relaxed: list[str] = []
    duration_band_idx = 0

    def _apply() -> list[PlayerLog]:
        pct = DURATION_BANDS_PCT[duration_band_idx]
        result = []
        for c in hygienic:
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
        adjustment_covariates=() if is_v1 else ("has_augmentation",),
        cohort_level=cohort_level,
    )


def match_cohort(
    target: PlayerLog,
    candidates: Sequence[PlayerLog],
    *,
    min_n: int = COHORT_TARGET_N,
    matching_policy_version: str = DEFAULT_MATCHING_POLICY_VERSION,
) -> tuple[list[PlayerLog], MatchReport]:
    """M2.3 §4.1: `hygienic_candidates(target, candidates)` composed with
    `match_covariates` on its output, with the hygiene counters copied into
    the returned `MatchReport` — byte-identical output to the pre-M2.3
    single-function `match_cohort` for any input. See `hygienic_candidates`
    and `match_covariates` for the two stages' own contracts.
    """
    hygienic, hygiene_report = hygienic_candidates(target, candidates)
    filtered, match_report = match_covariates(
        target, hygienic, min_n=min_n, matching_policy_version=matching_policy_version
    )
    return filtered, replace(
        match_report,
        excluded_self=hygiene_report.excluded_self,
        excluded_non_kill=hygiene_report.excluded_non_kill,
        deduped_pull=hygiene_report.deduped_pull,
        deduped_player=hygiene_report.deduped_player,
    )
