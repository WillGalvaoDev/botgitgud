"""SAE.3 — the experiment's feature contract
(docs/fase4-statistical-architecture-experiment.md §7, §9).

One registry, one role per column, so a cross-target model can tell contexts
apart and nothing that leaks can reach a model by accident.

Three decisions that shape this file:

**Spec-agnostic aggregates, not per-spell columns.** T3.1 stores
`cast_timeline`, `uptimes`, `damage_by_ability` and `avg_targets_per_cast`
as spell_id -> value maps. Those are *target-specific by construction*: a
Frost Mage and an Unholy DK share no spell ids, so per-spell columns cannot
transfer across specs and MODEL_SPEC/GLOBAL could never be evaluated. The
registry therefore exposes spec-agnostic summaries (counts, rates, means)
that mean the same thing for every spec. Per-spell detail stays in the
Parquet for a future target-only experiment.

**Only canonical identifiers.** `resource_waste` is keyed by localized
PT-BR labels ("Fragmentos de Alma") from domain/resource_types.py, and boss
names are display strings. Neither ever becomes a feature name or value;
waste is aggregated to a total, and context uses numeric ids plus the
canonical WCL class/spec spelling.

**Alignment score is deliberately absent.** T4.1 lists it as CONTROLLABLE,
but it is cohort-relative: computing it needs a reference profile built from
*other* logs of the same target. Built naively over the whole dataset it
would pull validation-period logs into a training row's feature — a leak
(§9). Adding it needs a per-fold cohort built strictly from the training
side, which is work for the experiment's execution, not its contract.
"""

from __future__ import annotations

from dataclasses import dataclass

from botgitgud.phase4.experiment import MODELLABLE_ROLES, FeatureRole


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    name: str
    role: FeatureRole
    description: str
    categorical: bool = False


# Order is stable and meaningful: identity, context, non-controllable,
# controllable, target. Downstream code builds column vectors from this.
FEATURE_REGISTRY: tuple[FeatureSpec, ...] = (
    # -- identity: dedup and split keys, never model inputs -------------------
    FeatureSpec("report_code", FeatureRole.IDENTITY, "WCL report code"),
    FeatureSpec("fight_id", FeatureRole.IDENTITY, "fight id within the report"),
    FeatureSpec("player_name", FeatureRole.IDENTITY, "character name"),
    FeatureSpec("observed_at_ms", FeatureRole.IDENTITY, "fight start, epoch ms; orders splits"),
    # -- context: lets a shared model tell targets apart ----------------------
    FeatureSpec("ctx_class", FeatureRole.CONTEXT, "canonical WCL class", categorical=True),
    FeatureSpec("ctx_spec", FeatureRole.CONTEXT, "canonical WCL spec", categorical=True),
    FeatureSpec("ctx_encounter_id", FeatureRole.CONTEXT, "WCL encounter id", categorical=True),
    FeatureSpec("ctx_difficulty", FeatureRole.CONTEXT, "3/4/5", categorical=True),
    FeatureSpec("ctx_partition", FeatureRole.CONTEXT, "WCL partition", categorical=True),
    # -- non-controllable: statistical control, never a recommendation --------
    FeatureSpec("nc_item_level", FeatureRole.NON_CONTROLLABLE, "player item level"),
    FeatureSpec("nc_tier_pieces", FeatureRole.NON_CONTROLLABLE, "count of tier-set pieces"),
    FeatureSpec("nc_duration_s", FeatureRole.NON_CONTROLLABLE, "fight duration in seconds"),
    FeatureSpec("nc_raid_size", FeatureRole.NON_CONTROLLABLE, "raid size (composition proxy)"),
    FeatureSpec(
        "nc_n_external_buffs", FeatureRole.NON_CONTROLLABLE, "distinct external buffs received"
    ),
    FeatureSpec(
        "nc_has_augmentation", FeatureRole.NON_CONTROLLABLE, "an Augmentation Evoker buffed player"
    ),
    # -- controllable: the only role that may ever become advice --------------
    FeatureSpec("c_active_time_pct", FeatureRole.CONTROLLABLE, "activeTime / fight duration"),
    FeatureSpec("c_deaths", FeatureRole.CONTROLLABLE, "own deaths"),
    FeatureSpec("c_downtime_s", FeatureRole.CONTROLLABLE, "seconds spent dead"),
    FeatureSpec("c_total_casts", FeatureRole.CONTROLLABLE, "casts across all abilities"),
    FeatureSpec("c_casts_per_minute", FeatureRole.CONTROLLABLE, "duration-normalized cast rate"),
    FeatureSpec("c_distinct_abilities_cast", FeatureRole.CONTROLLABLE, "distinct spells cast"),
    FeatureSpec("c_mean_uptime", FeatureRole.CONTROLLABLE, "mean uptime over tracked auras"),
    FeatureSpec("c_n_tracked_auras", FeatureRole.CONTROLLABLE, "auras with any uptime"),
    FeatureSpec("c_resource_waste_total", FeatureRole.CONTROLLABLE, "summed overcap across types"),
    FeatureSpec(
        "c_resource_waste_per_minute", FeatureRole.CONTROLLABLE, "duration-normalized waste"
    ),
    FeatureSpec(
        "c_mean_targets_per_cast", FeatureRole.CONTROLLABLE, "mean distinct targets per cast"
    ),
    # -- target ---------------------------------------------------------------
    FeatureSpec("y_rank_percent", FeatureRole.TARGET, "WCL rankPercent, 0-100"),
)

# Never emitted as columns. Named here so the exclusion is explicit and
# testable rather than an accident of omission (§9).
#
# `dps`/`amount` are the important ones: rankPercent IS the percentile of
# that DPS within the ranking pool, so a model given raw DPS would score
# almost perfectly and prove nothing at all.
EXCLUDED_LEAKAGE_COLUMNS: tuple[FeatureSpec, ...] = (
    FeatureSpec("dps", FeatureRole.EXCLUDED_LEAKAGE, "target by construction: rankPercent IS DPS%"),
    FeatureSpec("amount", FeatureRole.EXCLUDED_LEAKAGE, "same as dps, from discovery_targets"),
    FeatureSpec("bracket_percent", FeatureRole.EXCLUDED_LEAKAGE, "a variant of the target"),
    FeatureSpec("total_parses", FeatureRole.EXCLUDED_LEAKAGE, "character history, proxy identity"),
    FeatureSpec("percentile", FeatureRole.EXCLUDED_LEAKAGE, "raw alias of the target column"),
)


def features_with_role(role: FeatureRole) -> tuple[FeatureSpec, ...]:
    return tuple(spec for spec in FEATURE_REGISTRY if spec.role is role)


def modellable_features() -> tuple[FeatureSpec, ...]:
    """Every column a model may consume — context plus both controllable and
    non-controllable covariates. Identity, target and leakage are excluded by
    construction.
    """
    return tuple(spec for spec in FEATURE_REGISTRY if spec.role in MODELLABLE_ROLES)


def controllable_feature_names() -> tuple[str, ...]:
    """T4.2's inviolable rule: only these may ever become a recommendation."""
    return tuple(spec.name for spec in features_with_role(FeatureRole.CONTROLLABLE))


def feature_names() -> tuple[str, ...]:
    return tuple(spec.name for spec in FEATURE_REGISTRY)
