"""SAE.1 — domain contracts for the statistical architecture experiment
(docs/phase4.md).

Pure vocabulary: model granularities (A-E), split protocols (S1-S5),
percentile buckets and the experiment's API budget. No I/O, no API calls,
no model training — every other SAE module imports its terms from here so
the planner, the dataset layer and the split layer cannot drift apart.

The census (docs/phase4.md) measured that one model per
Phase4Target is arithmetically unaffordable: 662 supported targets, none
projected to reach 5.000 observations within 90 days. This module exists to
make the *alternatives* expressible so they can be compared empirically.

Nothing here decides a winner, and nothing here relaxes the Fase 4 gate of
5.000 (docs/phase4.md) — that gate governs the
final Fase 4, while this experiment deliberately trades depth for width.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from botgitgud.phase4.target import Phase4Target

# --- percentile stratification (doc §7) ---------------------------------------

# Fixed, half-open except the last: [0,20) [20,40) [40,60) [60,80) [80,100].
# Measured in the census warehouse, the raw candidate pool is already nearly
# uniform across these (4581/4894/5027/4914/5278), so stratifying costs
# little sample size while guaranteeing the sample is never "top parses only".
PERCENTILE_BUCKET_EDGES: tuple[int, ...] = (0, 20, 40, 60, 80)
PERCENTILE_BUCKETS: tuple[str, ...] = ("00-20", "20-40", "40-60", "60-80", "80-100")


def percentile_bucket(rank_percent: float) -> str:
    """Maps a WCL rankPercent (0-100) to its fixed stratification bucket.

    Raises on values outside [0, 100]: a rankPercent out of range means the
    upstream parse is wrong, and silently bucketing it would hide that.
    """
    if not 0.0 <= rank_percent <= 100.0:
        raise ValueError(f"rank_percent must be in [0, 100], got {rank_percent}")
    for index in range(len(PERCENTILE_BUCKET_EDGES) - 1, -1, -1):
        if rank_percent >= PERCENTILE_BUCKET_EDGES[index]:
            return PERCENTILE_BUCKETS[index]
    raise AssertionError("unreachable: edges start at 0")  # pragma: no cover


# --- model granularity (doc §5, alternatives A-E) -----------------------------


class ModelGranularity(StrEnum):
    """The five candidate architectures. `grouping_key` below is what makes
    them comparable: each granularity is just a different way of deciding
    which observations share a model.
    """

    MODEL_TARGET = "model_target"  # A — one model per Phase4Target
    MODEL_SPEC = "model_spec"  # B — one model per class/spec
    MODEL_ENCOUNTER = "model_encounter"  # C — one per encounter/difficulty/partition
    MODEL_GLOBAL = "model_global"  # D — a single model
    MODEL_HIERARCHICAL = "model_hierarchical"  # E — extension point only, see below


# E is a declared extension point, not an implementation. Building a
# multi-task/hierarchical model before knowing whether D (the simplest
# shared model) can even compete would add a framework dependency and
# training complexity with zero evidence behind it. Every consumer must
# reject this value explicitly rather than silently treating it as global.
IMPLEMENTED_GRANULARITIES: frozenset[ModelGranularity] = frozenset(
    {
        ModelGranularity.MODEL_TARGET,
        ModelGranularity.MODEL_SPEC,
        ModelGranularity.MODEL_ENCOUNTER,
        ModelGranularity.MODEL_GLOBAL,
    }
)

_GLOBAL_KEY = "global"


def grouping_key(granularity: ModelGranularity, target: Phase4Target) -> str:
    """Which model an observation belonging to `target` would be trained
    into, under `granularity`. Deterministic and path-safe, reusing
    Phase4Target's own canonical identity so keys never disagree with
    target_id.
    """
    if granularity is ModelGranularity.MODEL_TARGET:
        return target.target_id
    if granularity is ModelGranularity.MODEL_SPEC:
        return f"{target.spec.class_name}/{target.spec.spec_name}"
    if granularity is ModelGranularity.MODEL_ENCOUNTER:
        return f"{target.encounter_id}/{target.difficulty}/{target.partition}"
    if granularity is ModelGranularity.MODEL_GLOBAL:
        return _GLOBAL_KEY
    raise NotImplementedError(
        "MODEL_HIERARCHICAL is an extension point only "
        "(docs/phase4.md); "
        "A-D must produce results first"
    )


# --- split protocols (doc §8) -------------------------------------------------


class SplitProtocol(StrEnum):
    """Validation protocols. A simple random split is deliberately absent:
    it is never the primary validation here (doc §8).
    """

    S1_TEMPORAL_WITHIN_TARGET = "s1_temporal_within_target"
    S2_HELD_OUT_LOGS_TEMPORAL = "s2_held_out_logs_temporal"
    S3_HELD_OUT_ENCOUNTER = "s3_held_out_encounter"
    S4_HELD_OUT_SPEC = "s4_held_out_spec"
    S5_HELD_OUT_SPEC_ENCOUNTER = "s5_held_out_spec_encounter"


# --- feature roles (doc §9) ---------------------------------------------------


class FeatureRole(StrEnum):
    """Why a column exists. T4.1 mandates the CONTROLLABLE/NON_CONTROLLABLE
    split (only controllable features may ever become a recommendation);
    this experiment adds three roles it needs and T4.1 does not name:
    CONTEXT (lets a cross-target model tell contexts apart), IDENTITY
    (dedup/split keys, never a feature) and EXCLUDED_LEAKAGE.
    """

    TARGET = "target"
    IDENTITY = "identity"
    CONTEXT = "context"
    CONTROLLABLE = "controllable"
    NON_CONTROLLABLE = "non_controllable"
    EXCLUDED_LEAKAGE = "excluded_leakage"


# Roles that may be fed to a model. EXCLUDED_LEAKAGE is the important one:
# raw DPS predicts rankPercent by construction (rankPercent IS the percentile
# of that DPS within the ranking pool), so a model including it would score
# near-perfectly and mean nothing (doc §9).
MODELLABLE_ROLES: frozenset[FeatureRole] = frozenset(
    {FeatureRole.CONTEXT, FeatureRole.CONTROLLABLE, FeatureRole.NON_CONTROLLABLE}
)


# --- budget (doc §14) ---------------------------------------------------------

# Ceiling for the WHOLE experimental Stage C, not per run.
EXPERIMENT_MAX_API_POINTS = 25_000.0
# Measured cost model (docs/phase4.md): the event
# pages of a fight are fetched once and filtered client-side per player, so a
# second player from the same fight costs only the buffs/debuffs pair.
# 15 + 2 == 17 reproduces ingest/backfill_planner.py's measured solo-fight cost.
POINTS_PER_FIGHT_SHARED = 15.0
POINTS_PER_PLAYER_MARGINAL = 2.0

# Below this the experiment loses its object: S3 needs unseen encounters and
# S4 needs unseen specs, which a narrow sample cannot provide (doc §14).
MIN_ENCOUNTERS_FOR_EXPERIMENT = 5
MIN_SPECS_FOR_EXPERIMENT = 8


@dataclass(frozen=True, slots=True)
class ExperimentBudget:
    """An explicit, declared API ceiling. There is no implicit default at the
    call site: `StatisticalExperimentPlan` requires one, so no campaign can
    be produced without the caller stating what it may cost.
    """

    max_api_points: float = EXPERIMENT_MAX_API_POINTS
    points_per_fight_shared: float = POINTS_PER_FIGHT_SHARED
    points_per_player_marginal: float = POINTS_PER_PLAYER_MARGINAL

    def __post_init__(self) -> None:
        if self.max_api_points <= 0:
            raise ValueError("max_api_points must be positive")
        if self.points_per_fight_shared < 0 or self.points_per_player_marginal < 0:
            raise ValueError("point costs must be non-negative")

    def estimate_points(self, *, n_fights: int, n_observations: int) -> float:
        """Cost of extracting `n_observations` players spread over
        `n_fights` distinct fights. Diversity sampling spreads picks across
        many fights, so this usually approaches 17/observation; batching two
        players of one fight is correctly cheaper.
        """
        if n_fights < 0 or n_observations < 0:
            raise ValueError("counts must be non-negative")
        return (
            n_fights * self.points_per_fight_shared
            + n_observations * self.points_per_player_marginal
        )

    def max_observations_solo(self) -> int:
        """Upper bound assuming the worst case of one observation per fight
        — the honest number to quote when planning, since a diverse sample
        rarely reuses fights.
        """
        per_solo = self.points_per_fight_shared + self.points_per_player_marginal
        if per_solo <= 0:
            raise ValueError("solo observation cost must be positive")
        return int(self.max_api_points // per_solo)
