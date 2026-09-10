"""T3.2 — Oaxaca-Blinder-style decomposition of the player's DPS gap
against the cohort, per ability (docs/implementacao.md T3.2: "maior
retorno/esforço do roadmap").

For each ability `a`, n is its natural count: casts only when every log that
dealt damage with the ability has casts, otherwise hits. The same choice is
used for the player and every reference member so the counts remain
commensurable and no damage carrier is assigned a zero count. p is damage per
natural unit; absence contributes an explicit (0, 0), never a silently
skipped value.

    Δd(a)         = d_u(a) - d_r(a)
    volume(a)     = (n_u(a) - n_r(a)) * p_r(a)       — rotation/usage error
    efficiency(a) = (p_u(a) - p_r(a)) * n_r(a)       — window, buffs, gear, targets
    interaction(a) = (n_u(a) - n_r(a)) * (p_u(a) - p_r(a))

    volume + efficiency + interaction == Δd  (exact identity, not an
    approximation — see test_dps_gap.py's Hypothesis-generated proof).

Diagnosis rules (docs/implementacao.md T3.2, evaluated in this exact
order) exist because the efficiency term indiscriminately absorbs both
controllable causes (missing a burst window) and uncontrollable ones (no
Augmentation in the raid, a low-target fight) — rules 1/2 pull those two
uncontrollable cases out before rules 3/4 fall back to the generic
volume-vs-efficiency magnitude comparison.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from botgitgud.analysis.damage_scope_guard import select_comparable
from botgitgud.analysis.grading import compute_quantile_stats
from botgitgud.analysis.performance_features import ScalarFinding, grade_scalar
from botgitgud.domain.damage_scope import DamageScopeVersion
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
]

DIAGNOSIS_LABELS: dict[Diagnosis, str] = {
    "buffs_nao_pareados": (
        "dano por cast abaixo — buffs de suporte não pareados, possivelmente não controlável"
    ),
    "poucos_alvos": "menos alvos atingidos",
    "usos_perdidos_excedentes": "usos perdidos/excedentes",
    "janela_ou_buffs_proprios": "dano por cast abaixo — janela ou buffs próprios",
    "volume_e_eficiencia_combinados": "volume e eficiência combinados",
}


@dataclass(frozen=True, slots=True)
class AbilityGap:
    spell: SpellInfo
    n_u: float
    d_u: float
    p_u: float
    n_r: float
    p_r: float
    d_r: float
    delta_d: float
    volume: float
    efficiency: float
    interaction: float
    delta_dps_pct: float  # % of the player's own total DPS
    volume_dps_pct: float
    efficiency_dps_pct: float
    diagnosis: Diagnosis
    confidence: Literal["alta", "baixa"]  # downgraded only by rule 1 (unpaired buffs)
    unit_kind: Literal["CAST", "TICK_OR_PET_HIT"]
    n_ref: float = 0.0
    p_ref: float = 0.0
    delta_dps_pct_ref: float = 0.0
    # M29/RB-2: the ability's share of the PLAYER's own measured DPS,
    # graded (grade_scalar, "higher_better") against the SAME share
    # computed per matched member — never a substitute for delta_dps_pct/
    # the Oaxaca terms above, which this field never touches. `None` only
    # when there is no cohort to grade against (no matched member share
    # could be computed), never a materiality decision by itself — that
    # belongs to analysis/materiality.py.
    cohort_share: ScalarFinding | None = None


@dataclass(frozen=True, slots=True)
class DpsGapReport:
    player_dps: float
    cohort_median_dps: float | None
    gap_pct: float | None  # (player_dps - cohort_median_dps) / cohort_median_dps
    duration_s: float
    abilities: tuple[AbilityGap, ...]  # gated, sorted by |delta_dps_pct| descending
    other_pct: float  # signed sum of every ungated ability's delta_dps_pct
    n_other: int
    measured_dps: float = 0.0
    benchmark_reference_dps: float = 0.0
    damage_scope: DamageScopeVersion = DamageScopeVersion.LEGACY_UNSCOPED
    excluded_by_scope: int = 0
    quantitative_damage_available: bool = True


def _member_n_p(
    damage_by_ability: Mapping[int, AbilityDamage],
    spell_id: int,
    unit_kind: Literal["CAST", "TICK_OR_PET_HIT"],
) -> tuple[float, float]:
    ab = damage_by_ability.get(spell_id)
    if ab is None:
        return 0.0, 0.0
    n = ab.casts if unit_kind == "CAST" else ab.hits
    if n == 0:
        return 0.0, 0.0
    return float(n), ab.total / n


def _unit_kind(
    player_damage: Mapping[int, AbilityDamage],
    matched_logs: Sequence[PlayerLog],
    spell_id: int,
) -> Literal["CAST", "TICK_OR_PET_HIT"]:
    abilities = [
        ability
        for damage in (player_damage, *(log.damage_by_ability for log in matched_logs))
        if (ability := damage.get(spell_id)) is not None and ability.total > 0
    ]
    return (
        "CAST"
        if abilities and all(ability.casts > 0 for ability in abilities)
        else "TICK_OR_PET_HIT"
    )


def _measured_dps(log: PlayerLog) -> float:
    """Same measured-DPS definition `analyze_dps_gap` already applies to
    the player (positive ability totals minus support-subtracted damage,
    over fight duration) — mirrored per matched member so M29/RB-2 can
    grade each member's own ability share against their own DPS, never
    against raw damage (which would compare logs of different durations).
    """
    duration_s = log.fight.duration_s
    if not duration_s:
        return 0.0
    measured_damage = (
        sum(ability.total for ability in log.damage_by_ability.values() if ability.total > 0)
        - log.support_subtracted_damage
    )
    return measured_damage / duration_s


def oaxaca_terms(n_u: float, p_u: float, n_r: float, p_r: float) -> tuple[float, float, float]:
    """(volume, efficiency, interaction) — sums exactly to n_u*p_u - n_r*p_r."""
    volume = (n_u - n_r) * p_r
    efficiency = (p_u - p_r) * n_r
    interaction = (n_u - n_r) * (p_u - p_r)
    return volume, efficiency, interaction


def _diagnose(
    *,
    volume: float,
    efficiency: float,
    buffs_relaxed: bool,
    player_avg_targets: float | None,
    cohort_avg_targets: Sequence[float],
) -> tuple[Diagnosis, Literal["alta", "baixa"]]:
    dominant_is_efficiency = abs(efficiency) > abs(volume)

    if buffs_relaxed and dominant_is_efficiency:
        return "buffs_nao_pareados", "baixa"

    if dominant_is_efficiency and player_avg_targets is not None and cohort_avg_targets:
        p25 = compute_quantile_stats(cohort_avg_targets).p25
        if p25 is not None and player_avg_targets < p25:
            return "poucos_alvos", "alta"

    if abs(volume) > DIAGNOSIS_RATIO * abs(efficiency):
        return "usos_perdidos_excedentes", "alta"
    if abs(efficiency) > DIAGNOSIS_RATIO * abs(volume):
        return "janela_ou_buffs_proprios", "alta"
    return "volume_e_eficiencia_combinados", "alta"


def analyze_dps_gap(
    player_log: PlayerLog,
    matched_logs: Sequence[PlayerLog],
    *,
    cohort_median_dps: float | None,
    catalog: SpellCatalog,
    buffs_relaxed: bool,
    benchmark_reference: Sequence[PlayerLog] = (),
) -> DpsGapReport:
    comparable_logs, excluded_matched = select_comparable(player_log, matched_logs)
    comparable_benchmark, excluded_benchmark = select_comparable(player_log, benchmark_reference)
    excluded_by_scope = excluded_matched + excluded_benchmark
    quantitative_damage_available = (
        player_log.damage_scope is not DamageScopeVersion.UNRECONCILED
        and (not matched_logs or bool(comparable_logs))
    )
    duration_s = player_log.fight.duration_s
    player_dps = player_log.dps or 0.0
    gap_pct = (player_dps - cohort_median_dps) / cohort_median_dps if cohort_median_dps else None
    if not quantitative_damage_available:
        return DpsGapReport(
            player_dps=player_dps,
            cohort_median_dps=cohort_median_dps,
            gap_pct=gap_pct,
            duration_s=duration_s,
            abilities=(),
            other_pct=0.0,
            n_other=0,
            damage_scope=player_log.damage_scope,
            excluded_by_scope=excluded_by_scope,
            quantitative_damage_available=False,
        )

    matched_logs = comparable_logs
    benchmark_reference = comparable_benchmark
    measured_damage = (
        sum(ability.total for ability in player_log.damage_by_ability.values() if ability.total > 0)
        - player_log.support_subtracted_damage
    )
    measured_dps = measured_damage / duration_s if duration_s else 0.0
    # M29/RB-2: each matched member's own measured DPS, computed once
    # (identical for every ability) — never recomputed per spell_id.
    matched_measured_dps = [_measured_dps(rl) for rl in matched_logs]

    spell_ids = set(player_log.damage_by_ability)
    for rl in matched_logs:
        spell_ids |= set(rl.damage_by_ability)

    gated: list[AbilityGap] = []
    other_pct = 0.0
    n_other = 0
    for spell_id in sorted(spell_ids):
        unit_kind = _unit_kind(player_log.damage_by_ability, matched_logs, spell_id)
        n_u, p_u = _member_n_p(player_log.damage_by_ability, spell_id, unit_kind)
        d_u = n_u * p_u

        n_list: list[float] = []
        p_list: list[float] = []
        for rl in matched_logs:
            n_i, p_i = _member_n_p(rl.damage_by_ability, spell_id, unit_kind)
            n_list.append(n_i)
            p_list.append(p_i)
        n_r = statistics.median(n_list) if n_list else 0.0
        p_r = statistics.median(p_list) if p_list else 0.0
        d_r = n_r * p_r
        delta_d = d_u - d_r

        volume, efficiency, interaction = oaxaca_terms(n_u, p_u, n_r, p_r)

        delta_dps_pct = (delta_d / duration_s / measured_dps * 100) if measured_dps else 0.0

        ref_n_values: list[float] = []
        ref_p_values: list[float] = []
        for rl in benchmark_reference:
            n_i, p_i = _member_n_p(rl.damage_by_ability, spell_id, unit_kind)
            ref_n_values.append(n_i)
            ref_p_values.append(p_i)
        n_ref = statistics.median(ref_n_values) if ref_n_values else 0.0
        p_ref = statistics.median(ref_p_values) if ref_p_values else 0.0
        # Use the same model (including its interaction identity), with only
        # the reference population changed.
        ref_volume, ref_efficiency, ref_interaction = oaxaca_terms(n_u, p_u, n_ref, p_ref)
        delta_ref = ref_volume + ref_efficiency + ref_interaction
        delta_dps_pct_ref = delta_ref / duration_s / measured_dps * 100 if measured_dps else 0.0

        if catalog.identity(spell_id).resolution_status == "unresolved":
            other_pct += delta_dps_pct
            n_other += 1
            continue

        if abs(delta_dps_pct) < IMPACT_GATE_PCT:
            other_pct += delta_dps_pct
            n_other += 1
            continue

        cohort_avg_targets = [
            rl.avg_targets_per_cast[spell_id]
            for rl in matched_logs
            if spell_id in rl.avg_targets_per_cast
        ]
        player_avg_targets = player_log.avg_targets_per_cast.get(spell_id)
        diagnosis, confidence = _diagnose(
            volume=volume,
            efficiency=efficiency,
            buffs_relaxed=buffs_relaxed,
            player_avg_targets=player_avg_targets,
            cohort_avg_targets=cohort_avg_targets,
        )

        # M29/RB-2: recover the per-member distribution `n_list`/`p_list`
        # already built above (instead of discarding it after the median),
        # grading the ability's share of measured DPS — player vs each
        # matched member's OWN share — with the same grade_scalar M27
        # already uses. A second, additive door for coaching materiality;
        # never a substitute for delta_dps_pct/IMPACT_GATE_PCT above.
        shares_cohort = [
            n_i * p_i / rl.fight.duration_s / member_dps * 100
            for rl, n_i, p_i, member_dps in zip(
                matched_logs, n_list, p_list, matched_measured_dps, strict=True
            )
            if rl.fight.duration_s and member_dps
        ]
        share_player = (d_u / duration_s / measured_dps * 100) if measured_dps else 0.0
        cohort_share = (
            grade_scalar(share_player, shares_cohort, "higher_better") if shares_cohort else None
        )

        gated.append(
            AbilityGap(
                spell=catalog.get(spell_id),
                n_u=n_u,
                d_u=d_u,
                p_u=p_u,
                n_r=n_r,
                p_r=p_r,
                d_r=d_r,
                delta_d=delta_d,
                volume=volume,
                efficiency=efficiency,
                interaction=interaction,
                delta_dps_pct=delta_dps_pct,
                volume_dps_pct=(volume / duration_s / measured_dps * 100) if measured_dps else 0.0,
                efficiency_dps_pct=(
                    (efficiency / duration_s / measured_dps * 100) if measured_dps else 0.0
                ),
                diagnosis=diagnosis,
                confidence=confidence,
                unit_kind=unit_kind,
                n_ref=n_ref,
                p_ref=p_ref,
                delta_dps_pct_ref=delta_dps_pct_ref,
                cohort_share=cohort_share,
            )
        )

    gated.sort(key=lambda a: abs(a.delta_dps_pct), reverse=True)

    return DpsGapReport(
        player_dps=player_dps,
        cohort_median_dps=cohort_median_dps,
        gap_pct=gap_pct,
        duration_s=duration_s,
        abilities=tuple(gated),
        other_pct=other_pct,
        n_other=n_other,
        measured_dps=measured_dps,
        benchmark_reference_dps=(
            statistics.median(log.dps for log in benchmark_reference if log.dps is not None)
            if any(log.dps is not None for log in benchmark_reference)
            else 0.0
        ),
        damage_scope=player_log.damage_scope,
        excluded_by_scope=excluded_by_scope,
        quantitative_damage_available=True,
    )
