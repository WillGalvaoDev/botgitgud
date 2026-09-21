"""M1: paired arithmetic-mean contrasts of measured net DPS.

Damage-event rate, per-event damage and interaction describe compatible pairs;
unsupported splits remain unclassified. None of these terms identifies a cause.
Active components are expressed in DPS; historical damage adapters are not produced.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

from botgitgud.analysis.ability_classification import classify_abilities
from botgitgud.analysis.measurement import (
    DamageComparison,
    MetricComparison,
    MetricObservation,
    MetricStatus,
    account_damage,
    compare_damage,
    damage_reference_id,
    measured_median,
)
from botgitgud.analysis.metric_observations import compare_metrics
from botgitgud.analysis.performance_features import ScalarFinding
from botgitgud.domain.ability_role import AbilityRole
from botgitgud.domain.damage_scope import DamageScopeVersion
from botgitgud.domain.measurement_validation import valid_player_casts
from botgitgud.domain.models import AbilityDamage, PlayerLog
from botgitgud.domain.spells import SpellCatalog, SpellInfo

# T3.2: "só listar habilidades com |Δd| >= 0,5% do DPS total do jogador" (6.3g).
IMPACT_GATE_PCT = 0.5
# T3.2 rules 3/4: "|volume| > 2 x |eficiência|" and the symmetric case.
DIAGNOSIS_RATIO = 2.0

Diagnosis = Literal[
    "buffs_nao_pareados",
    "poucos_alvos",
    "usos_perdidos_excedentes",
    "janela_ou_buffs_proprios",
    "volume_e_eficiencia_combinados",
    "observed_output_deficit",
]

DIAGNOSIS_LABELS: dict[Diagnosis, str] = {
    "buffs_nao_pareados": (
        "dano por cast abaixo — buffs de suporte não pareados, possivelmente não controlável"
    ),
    "poucos_alvos": "menos alvos atingidos",
    "usos_perdidos_excedentes": "usos perdidos/excedentes",
    "janela_ou_buffs_proprios": "dano por cast abaixo — janela ou buffs próprios",
    "volume_e_eficiencia_combinados": "volume e eficiência combinados",
    "observed_output_deficit": "déficit de saída observado (causa não identificada)",
}


@dataclass(frozen=True, slots=True)
class AbilityGap:
    spell: SpellInfo
    delta_dps_pct: float | None
    volume_dps_pct: float | None
    efficiency_dps_pct: float | None
    diagnosis: Diagnosis
    confidence: Literal["alta", "baixa"]
    unit_kind: Literal["DAMAGE_EVENT"]
    volume_dps: float = 0.0
    interaction_dps: float = 0.0
    interaction_dps_pct: float | None = None
    unclassified_dps_pct: float | None = None
    gross_dps_finding: ScalarFinding | None = None
    gross_damage_share_pct: float = 0.0
    per_event_dps: float = 0.0
    unclassified_dps: float = 0.0
    split_pair_count: int = 0
    split_pair_reasons: tuple[str, ...] = ()
    review_eligible: bool = False
    player_ability_dps: float = 0.0
    reference_mean_ability_dps: float = 0.0
    delta_ability_dps: float = 0.0
    gross_dps_observation: MetricObservation | None = None
    gross_dps_comparison: MetricComparison | None = None


@dataclass(frozen=True, slots=True)
class DpsGapReport:
    player_dps: float | None
    cohort_median_dps: float | None
    gap_vs_reference_pct: float | None  # 100 * measured net DPS delta / measured reference mean
    duration_s: float
    abilities: tuple[AbilityGap, ...]  # gated, sorted by |delta_dps_pct| descending
    other_pct: float | None  # signed sum of every ungated ability's delta_dps_pct
    n_other: int
    measured_dps: float | None = None
    reference_mean_dps: float | None = None
    benchmark_reference_dps: float | None = None
    damage_scope: DamageScopeVersion = DamageScopeVersion.LEGACY_UNSCOPED
    excluded_by_scope: int = 0
    quantitative_damage_available: bool = True
    # M1 accounting/comparison metadata, with explicit versions and units.
    comparison_method: str = "paired_mean_v2"
    reference_n_quantitative: int = 0
    support_delta_dps: float | None = None
    total_delta_dps: float | None = None
    total_delta_player_pp: float | None = None
    other_delta_dps: float = 0.0
    unclassified_dps: float = 0.0
    accounting_status: str = "UNKNOWN"
    comparison: DamageComparison | None = None
    aspirational_comparison: DamageComparison | None = None
    metric_comparisons: Mapping[str, MetricComparison] = field(default_factory=dict)
    entity_review_eligible: frozenset[int] = frozenset()
    comparison_reasons: tuple[str, ...] = ()


def _closes(terms: Sequence[float], expected: float) -> bool:
    values = (*terms, -expected)
    if not all(math.isfinite(value) for value in values):
        return False
    try:
        return abs(math.fsum(values)) <= max(1e-9, math.fsum(abs(v) * 1e-12 for v in values))
    except OverflowError:
        return False


def _sum(values: Sequence[float]) -> float:
    try:
        return math.fsum(values)
    except (OverflowError, ValueError):
        return math.inf


def oaxaca_terms(n_u: float, p_u: float, n_r: float, p_r: float) -> tuple[float, float, float]:
    """(volume, efficiency, interaction) — sums exactly to n_u*p_u - n_r*p_r."""
    volume = (n_u - n_r) * p_r
    efficiency = (p_u - p_r) * n_r
    interaction = (n_u - n_r) * (p_u - p_r)
    return volume, efficiency, interaction


def _event_split_pair(
    player: PlayerLog, reference: PlayerLog, spell_id: int
) -> tuple[tuple[float, float, float] | None, str | None]:
    """Return event-rate/per-event split only for a single compatible bucket."""
    pu = player.measurement_provenance
    pr = reference.measurement_provenance
    au = player.damage_by_ability.get(spell_id)
    ar = reference.damage_by_ability.get(spell_id)
    if (
        pu is None
        or pr is None
        or au is None
        or ar is None
        or not math.isfinite(au.hits)
        or not math.isfinite(ar.hits)
        or au.hits <= 0
        or ar.hits <= 0
        or au.hits != int(au.hits)
        or ar.hits != int(ar.hits)
        or player.fight.duration_s <= 0
        or reference.fight.duration_s <= 0
    ):
        return None, "PROVENANCE_OR_HITS_UNAVAILABLE"
    bu = pu.damage_event_mix_by_spell.get(spell_id, {})
    br = pr.damage_event_mix_by_spell.get(spell_id, {})
    for buckets, ability in ((bu, au), (br, ar)):
        if any(
            not math.isfinite(v.damage)
            or v.damage < 0
            or not math.isfinite(v.count)
            or v.count < 0
            or v.count != int(v.count)
            or (v.count == 0 and v.damage != 0)
            for v in buckets.values()
        ):
            return None, "INVALID_EVENT_BUCKET"
        if (
            sum(v.count for v in buckets.values()) != ability.hits
            or _sum([v.damage for v in buckets.values()]) != ability.total
        ):
            return None, "EVENT_MIX_RECONCILIATION_MISMATCH"
    allowed = {"PLAYER:TRUE", "PLAYER:FALSE", "PET:TRUE", "PET:FALSE"}
    valid = {k for k, v in bu.items() if v.count > 0}
    valid_r = {k for k, v in br.items() if v.count > 0}
    if not valid.issubset(allowed) or not valid_r.issubset(allowed):
        return None, "UNKNOWN_OR_UNSUPPORTED_EVENT_BUCKET"
    if len(valid) != 1 or valid != valid_r:
        return None, "MIXED_OR_INCOMPATIBLE_EVENT_BUCKET"
    key = next(iter(valid))
    if bu[key].count != au.hits or br[key].count != ar.hits:
        return None, "EVENT_COUNT_MISMATCH"
    if bu[key].damage != au.total or br[key].damage != ar.total:
        return None, "EVENT_DAMAGE_MISMATCH"
    qu, qr = au.hits / player.fight.duration_s, ar.hits / reference.fight.duration_s
    p_u, p_r = au.total / au.hits, ar.total / ar.hits
    return oaxaca_terms(qu, p_u, qr, p_r), None


def _review_eligible(player: PlayerLog, spell_id: int, role: AbilityRole | None) -> bool:
    allowed = {
        AbilityRole.CORE_DAMAGE,
        AbilityRole.SECONDARY_DAMAGE,
        AbilityRole.OFFENSIVE_COOLDOWN,
        AbilityRole.RESOURCE_GENERATOR,
        AbilityRole.RESOURCE_SPENDER,
    }
    provenance = player.measurement_provenance
    ability = player.damage_by_ability.get(spell_id)
    mix = provenance.damage_event_mix_by_spell.get(spell_id, {}) if provenance else {}
    if (
        role not in allowed
        or not valid_player_casts(player, spell_id)
        or ability is None
        or not mix
    ):
        return False
    return (
        all(key.startswith("PLAYER:") and value.count > 0 for key, value in mix.items())
        and all(
            math.isfinite(value.count)
            and value.count == int(value.count)
            and math.isfinite(value.damage)
            and value.damage >= 0
            for value in mix.values()
        )
        and sum(value.count for value in mix.values()) == ability.hits
        and _sum([value.damage for value in mix.values()]) == ability.total
    )


def _diagnose(**_: object) -> tuple[Diagnosis, Literal["alta", "baixa"]]:
    """Compatibility for the retired pre-M1 calculation path."""
    return "observed_output_deficit", "baixa"


def analyze_dps_gap(
    player_log: PlayerLog,
    matched_logs: Sequence[PlayerLog],
    *,
    cohort_median_dps: float | None,
    catalog: SpellCatalog,
    buffs_relaxed: bool,
    benchmark_reference: Sequence[PlayerLog] = (),
) -> DpsGapReport:
    return _analyze_dps_gap_v2(
        player_log,
        matched_logs,
        cohort_median_dps=cohort_median_dps,
        catalog=catalog,
        benchmark_reference=benchmark_reference,
    )


def _analyze_dps_gap_v2(
    player: PlayerLog,
    references: Sequence[PlayerLog],
    *,
    cohort_median_dps: float | None,
    catalog: SpellCatalog,
    benchmark_reference: Sequence[PlayerLog],
) -> DpsGapReport:
    """M1 authoritative path: one accounting population and one DPS ledger."""
    accounting = account_damage(player)
    metrics = compare_metrics(player, references, catalog)
    comparison = compare_damage(player, tuple(references))
    aspirational = compare_damage(player, tuple(benchmark_reference))
    duration = player.fight.duration_s
    player_dps = player.dps  # WCL context only.
    by_id = {damage_reference_id(item): item for item in references}
    eligible = tuple(by_id[item] for item in comparison.reference_ids)
    measured_values = [
        value for item in eligible if (value := account_damage(item).net_dps) is not None
    ]
    cohort_median_dps = measured_median(measured_values)
    gap_vs_reference_pct = comparison.gap_vs_reference_pct

    quantitative = (
        accounting.status is MetricStatus.AVAILABLE
        and comparison.status is not MetricStatus.INVALID
    )
    excluded_scope = sum(
        reason in {"SCOPE_MISMATCH", "NON_QUANTITATIVE_SCOPE"}
        for reason in (
            *comparison.excluded_references.values(),
            *aspirational.excluded_references.values(),
        )
    )
    base = DpsGapReport(
        abilities=(),
        other_pct=None,
        n_other=0,
        metric_comparisons=metrics,
        player_dps=player_dps,
        cohort_median_dps=cohort_median_dps,
        gap_vs_reference_pct=gap_vs_reference_pct,
        duration_s=duration,
        damage_scope=player.damage_scope,
        excluded_by_scope=excluded_scope,
        quantitative_damage_available=quantitative,
        comparison_method=comparison.measurement_version,
        reference_n_quantitative=comparison.reference_n,
        support_delta_dps=comparison.support_delta_dps,
        total_delta_dps=comparison.total_delta_dps,
        total_delta_player_pp=comparison.total_delta_player_pp,
        comparison=comparison,
        aspirational_comparison=aspirational,
        measured_dps=accounting.net_dps,
        reference_mean_dps=comparison.reference_mean_net_dps,
        benchmark_reference_dps=aspirational.reference_mean_net_dps,
        comparison_reasons=comparison.reasons,
    )
    if not quantitative:
        return replace(
            base,
            abilities=(),
            other_pct=None,
            n_other=0,
            accounting_status=(
                comparison.status.value
                if comparison.status is MetricStatus.INVALID
                else accounting.status.value
            ),
        )

    classifications = classify_abilities(player)
    base = replace(
        base,
        entity_review_eligible=frozenset(
            sid
            for sid, classification in classifications.items()
            if catalog.identity(sid).resolution_status != "unresolved"
            and _review_eligible(player, sid, classification.role)
        ),
    )
    spell_ids = set(accounting.gross_damage_by_ability)
    for item in eligible:
        spell_ids.update(account_damage(item).gross_damage_by_ability)

    abilities: list[AbilityGap] = []
    other_dps = 0.0
    n_other = 0
    unclassified_total = 0.0
    for spell_id in sorted(spell_ids):
        player_ability = player.damage_by_ability.get(spell_id)
        d_u = max(player_ability.total, 0.0) if player_ability else 0.0
        ref_dps = tuple(
            max(item.damage_by_ability.get(spell_id, AbilityDamage(spell_id, 0, 0, 0)).total, 0)
            / item.fight.duration_s
            for item in eligible
        )
        reference_mean = math.fsum(value / len(ref_dps) for value in ref_dps) if ref_dps else 0.0
        player_ability_dps = d_u / duration
        delta_dps = comparison.ability_delta_dps.get(spell_id, 0.0)
        delta_pp = (
            100 * (delta_dps / accounting.net_dps)
            if accounting.net_dps is not None and accounting.net_dps > 0
            else None
        )

        pair_terms: list[tuple[float, float, float] | None] = []
        split_reasons: list[str] = []
        unclassified_pairs: list[float] = []
        closure_valid = True
        for item, ref_value in zip(eligible, ref_dps, strict=True):
            terms, reason = _event_split_pair(player, item, spell_id)
            pair_delta = player_ability_dps - ref_value
            pair_terms.append(terms)
            if terms is None:
                split_reasons.append(reason or "UNCLASSIFIED")
                unclassified_pairs.append(pair_delta)
            else:
                closure_valid &= _closes(terms, pair_delta)
        divisor = len(eligible) or 1
        volume = _sum([term[0] / divisor for term in pair_terms if term is not None])
        efficiency = _sum([term[1] / divisor for term in pair_terms if term is not None])
        interaction = _sum([term[2] / divisor for term in pair_terms if term is not None])
        unclassified = math.fsum(value / divisor for value in unclassified_pairs)
        closure_valid &= _closes((volume, efficiency, interaction, unclassified), delta_dps)
        if accounting.net_dps:
            closure_valid &= all(
                math.isfinite(100 * (value / accounting.net_dps))
                for value in (delta_dps, volume, efficiency, interaction, unclassified)
            )
        if not closure_valid:
            invalid = replace(
                comparison,
                status=MetricStatus.INVALID,
                reasons=("EVENT_SPLIT_CLOSURE_ERROR",),
                total_delta_dps=None,
                ability_delta_dps={},
                support_delta_dps=None,
                residual_dps=None,
                total_delta_player_pp=None,
                support_delta_player_pp=None,
                gap_vs_reference_pct=None,
            )
            base = replace(
                base,
                comparison=invalid,
                quantitative_damage_available=False,
                total_delta_dps=None,
                support_delta_dps=None,
                total_delta_player_pp=None,
                gap_vs_reference_pct=None,
                entity_review_eligible=frozenset(),
                comparison_reasons=invalid.reasons,
            )
            return replace(
                base, abilities=(), other_pct=None, n_other=0, accounting_status="INVALID"
            )
        unclassified_total = math.fsum((unclassified_total, unclassified))

        metric_comparison = metrics[f"gross_ability_dps:{spell_id}"]
        observation = metric_comparison.player
        scalar = metric_comparison.finding
        identity = catalog.identity(spell_id)
        classification = classifications.get(spell_id)
        role = classification.role if classification is not None else None
        eligible_for_review = (
            identity.resolution_status != "unresolved"
            and _review_eligible(player, spell_id, role)
            and scalar is not None
            and scalar.grade in {"red", "yellow"}
            and delta_dps < 0
            and (delta_pp is None or abs(delta_pp) >= IMPACT_GATE_PCT)
        )
        if (
            (observation.status is not MetricStatus.AVAILABLE and accounting.net_dps == 0)
            or (identity.resolution_status == "unresolved")
            or (delta_pp is not None and abs(delta_pp) < IMPACT_GATE_PCT)
        ):
            other_dps += delta_dps
            n_other += 1
            continue
        abilities.append(
            AbilityGap(
                spell=catalog.get(spell_id),
                volume_dps=volume,
                interaction_dps=interaction,
                delta_dps_pct=delta_pp,
                volume_dps_pct=100 * (volume / accounting.net_dps) if accounting.net_dps else None,
                efficiency_dps_pct=(
                    100 * (efficiency / accounting.net_dps) if accounting.net_dps else None
                ),
                interaction_dps_pct=(
                    100 * (interaction / accounting.net_dps) if accounting.net_dps else None
                ),
                unclassified_dps_pct=(
                    100 * (unclassified / accounting.net_dps) if accounting.net_dps else None
                ),
                diagnosis="observed_output_deficit",
                confidence="baixa",
                unit_kind="DAMAGE_EVENT",
                gross_dps_finding=scalar,
                gross_damage_share_pct=100 * (d_u / accounting.gross_damage_total)
                if accounting.gross_damage_total
                else 0.0,
                per_event_dps=efficiency,
                unclassified_dps=unclassified,
                split_pair_count=sum(term is not None for term in pair_terms),
                split_pair_reasons=tuple(split_reasons),
                review_eligible=eligible_for_review,
                player_ability_dps=player_ability_dps,
                reference_mean_ability_dps=reference_mean,
                delta_ability_dps=delta_dps,
                gross_dps_observation=observation,
                gross_dps_comparison=metric_comparison,
            )
        )

    abilities.sort(key=lambda item: (-abs(item.delta_ability_dps), item.spell.spell_id))
    if not _closes(
        (*[a.delta_ability_dps for a in abilities], other_dps, comparison.support_delta_dps or 0.0),
        comparison.total_delta_dps or 0.0,
    ):
        invalid = replace(
            comparison,
            status=MetricStatus.INVALID,
            reasons=("PRESENTATION_CLOSURE_ERROR",),
            ability_delta_dps={},
            total_delta_dps=None,
            support_delta_dps=None,
            residual_dps=None,
            total_delta_player_pp=None,
            support_delta_player_pp=None,
            gap_vs_reference_pct=None,
        )
        base = replace(
            base,
            comparison=invalid,
            quantitative_damage_available=False,
            total_delta_dps=None,
            support_delta_dps=None,
            total_delta_player_pp=None,
            gap_vs_reference_pct=None,
            entity_review_eligible=frozenset(),
            comparison_reasons=invalid.reasons,
        )
        return replace(base, abilities=(), other_pct=None, n_other=0, accounting_status="INVALID")

    return replace(
        base,
        abilities=tuple(abilities),
        other_pct=100 * (other_dps / accounting.net_dps) if accounting.net_dps else None,
        other_delta_dps=other_dps,
        n_other=n_other,
        unclassified_dps=unclassified_total,
        accounting_status=(
            "NO_REFERENCES"
            if not eligible
            else "INSUFFICIENT_REFERENCES"
            if len(eligible) < 8
            else "AVAILABLE"
        ),
    )
