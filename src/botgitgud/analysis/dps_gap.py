"""T3.2 — Oaxaca-Blinder-style decomposition of the player's DPS gap
against the cohort, per ability (docs/implementacao.md T3.2: "maior
retorno/esforço do roadmap").

For each ability `a`:
    c_u(a) = player's own cast count; p_u(a) = d_u(a)/c_u(a) (0 if c_u=0)
    c_r(a)/p_r(a) = cohort MEDIAN cast count / damage-per-cast — each
        member's own p_i is computed the same way (0 if that member never
        cast it), matching T3.1's own "absence contributes an explicit 0,
        never silently skipped" convention (analysis/profile.py).

    Δd(a)         = d_u(a) - d_r(a)
    volume(a)     = (c_u(a) - c_r(a)) * p_r(a)       — rotation/usage error
    efficiency(a) = (p_u(a) - p_r(a)) * c_r(a)       — window, buffs, gear, targets
    interaction(a) = (c_u(a) - c_r(a)) * (p_u(a) - p_r(a))

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

from botgitgud.analysis.grading import compute_quantile_stats
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
    c_u: float
    d_u: float
    p_u: float
    c_r: float
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


@dataclass(frozen=True, slots=True)
class DpsGapReport:
    player_dps: float
    cohort_median_dps: float | None
    gap_pct: float | None  # (player_dps - cohort_median_dps) / cohort_median_dps
    duration_s: float
    abilities: tuple[AbilityGap, ...]  # gated, sorted by |delta_dps_pct| descending
    other_pct: float  # signed sum of every ungated ability's delta_dps_pct
    n_other: int


def _member_c_p(
    damage_by_ability: Mapping[int, AbilityDamage], spell_id: int
) -> tuple[float, float]:
    ab = damage_by_ability.get(spell_id)
    if ab is None or ab.casts == 0:
        return 0.0, 0.0
    return float(ab.casts), ab.total / ab.casts


def oaxaca_terms(c_u: float, p_u: float, c_r: float, p_r: float) -> tuple[float, float, float]:
    """(volume, efficiency, interaction) — sums exactly to c_u*p_u - c_r*p_r."""
    volume = (c_u - c_r) * p_r
    efficiency = (p_u - p_r) * c_r
    interaction = (c_u - c_r) * (p_u - p_r)
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
) -> DpsGapReport:
    duration_s = player_log.fight.duration_s
    player_dps = player_log.dps or 0.0
    gap_pct = (player_dps - cohort_median_dps) / cohort_median_dps if cohort_median_dps else None

    spell_ids = set(player_log.damage_by_ability)
    for rl in matched_logs:
        spell_ids |= set(rl.damage_by_ability)

    gated: list[AbilityGap] = []
    other_pct = 0.0
    n_other = 0
    for spell_id in sorted(spell_ids):
        c_u, p_u = _member_c_p(player_log.damage_by_ability, spell_id)
        d_u = c_u * p_u

        c_list: list[float] = []
        p_list: list[float] = []
        for rl in matched_logs:
            c_i, p_i = _member_c_p(rl.damage_by_ability, spell_id)
            c_list.append(c_i)
            p_list.append(p_i)
        c_r = statistics.median(c_list) if c_list else 0.0
        p_r = statistics.median(p_list) if p_list else 0.0
        d_r = c_r * p_r
        delta_d = d_u - d_r

        volume, efficiency, interaction = oaxaca_terms(c_u, p_u, c_r, p_r)

        delta_dps_pct = (delta_d / duration_s / player_dps * 100) if player_dps else 0.0

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

        gated.append(
            AbilityGap(
                spell=catalog.get(spell_id),
                c_u=c_u,
                d_u=d_u,
                p_u=p_u,
                c_r=c_r,
                p_r=p_r,
                d_r=d_r,
                delta_d=delta_d,
                volume=volume,
                efficiency=efficiency,
                interaction=interaction,
                delta_dps_pct=delta_dps_pct,
                volume_dps_pct=(volume / duration_s / player_dps * 100) if player_dps else 0.0,
                efficiency_dps_pct=(
                    (efficiency / duration_s / player_dps * 100) if player_dps else 0.0
                ),
                diagnosis=diagnosis,
                confidence=confidence,
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
    )
