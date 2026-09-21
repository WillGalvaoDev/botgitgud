"""T3.1 — quantile-graded findings for the performance features beyond
casts (docs/implementacao.md T3.1, items 1/3/2/5 of its priority table:
active_time_pct, uptimes, deaths/downtime_s, resource_waste — items 3
`damage_by_ability` and 6 `avg_targets_per_cast` feed T3.2 instead, they
have no report section of their own here).

Reuses analysis/grading.py's T2.3 machinery (empirical quantile, bootstrap
median CI, MIN_N_FOR_GRADING suppression) but grades one-tailed instead of
two: a CD timing MATCH is bad whether it's early or late (grade_deviation
flags both tails), but these features have a single "good" direction —
more active time/uptime is good, more deaths/downtime/waste is bad. Being
*better* than the cohort's typical value must never be flagged red.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from botgitgud.analysis.grading import (
    MIN_N_FOR_GRADING,
    Grade,
    QuantileStats,
    bootstrap_median_ci,
    compute_quantile_stats,
    empirical_quantile,
)
from botgitgud.analysis.measurement import MetricComparison
from botgitgud.domain.models import PlayerLog
from botgitgud.domain.spells import SpellCatalog, SpellInfo

Direction = Literal["higher_better", "lower_better"]

# T3.1: "só buffs presentes em >=70% da coorte" (implementacao.md's own
# threshold for the uptimes feature).
UPTIME_PRESENCE_THRESHOLD = 0.70


@dataclass(frozen=True, slots=True)
class ScalarFinding:
    grade: Grade
    quantile: float | None
    user_value: float
    stats: QuantileStats
    ci90: tuple[float, float] | None
    direction: Direction


def scalar_is_finite(scalar: ScalarFinding) -> bool:
    values = (
        scalar.user_value,
        scalar.quantile,
        scalar.stats.p10,
        scalar.stats.p25,
        scalar.stats.p50,
        scalar.stats.p75,
        scalar.stats.p90,
        *(scalar.ci90 or ()),
    )
    return all(value is None or math.isfinite(value) for value in values)


def _grade_one_tailed(q: float, direction: Direction) -> Grade:
    # bad_q close to 0 => deep in the "worse than the cohort" tail,
    # regardless of which raw direction "worse" points in.
    bad_q = q if direction == "higher_better" else 1.0 - q
    if bad_q < 0.10:
        return "red"
    if bad_q < 0.25:
        return "yellow"
    return "green"


def grade_scalar(
    value: float, reference_values: Sequence[float], direction: Direction
) -> ScalarFinding:
    quantile = empirical_quantile(value, reference_values)
    if len(reference_values) < MIN_N_FOR_GRADING or quantile is None:
        grade: Grade = "insufficient"
    else:
        grade = _grade_one_tailed(quantile, direction)
    return ScalarFinding(
        grade=grade,
        quantile=quantile,
        user_value=value,
        stats=compute_quantile_stats(reference_values),
        ci90=bootstrap_median_ci(reference_values),
        direction=direction,
    )


@dataclass(frozen=True, slots=True)
class UptimeFinding:
    spell: SpellInfo
    finding: ScalarFinding
    comparison: MetricComparison | None = None


@dataclass(frozen=True, slots=True)
class WasteFinding:
    resource_type: str
    finding: ScalarFinding


@dataclass(frozen=True, slots=True)
class PerformanceFindings:
    active_time: ScalarFinding | None
    deaths: ScalarFinding
    downtime: ScalarFinding
    uptimes: tuple[UptimeFinding, ...]
    resource_waste: tuple[WasteFinding, ...]


def _build_uptime_findings(
    player_log: PlayerLog, matched_logs: Sequence[PlayerLog], catalog: SpellCatalog
) -> tuple[UptimeFinding, ...]:
    from botgitgud.analysis.metric_observations import compare_metrics

    comparisons = compare_metrics(
        player_log, matched_logs, catalog, metric_names=("aura_uptime_fraction",)
    )
    n = len(matched_logs)
    if n == 0:
        return ()
    spell_ids = set(player_log.uptimes)
    for rl in matched_logs:
        spell_ids |= set(rl.uptimes)

    findings: list[UptimeFinding] = []
    for spell_id in sorted(spell_ids):
        if catalog.identity(spell_id).resolution_status == "unresolved":
            continue
        presence = sum(1 for rl in matched_logs if spell_id in rl.uptimes) / n
        if presence < UPTIME_PRESENCE_THRESHOLD:
            continue
        # EC.1: a distribuição de referência usa só quem REALMENTE tem
        # este buff/debuff (subgroup-with-spell) — nunca `.get(spell_id,
        # 0.0)` preenchendo com zero quem não tem o mecanismo. Diferente
        # de casts (profile.py, situacional mesmo dentro do mesmo build),
        # ausência de uptime geralmente significa ausência do MECANISMO em
        # si — uma vez que o cohort deixar de ser homogêneo por build
        # (EC.3), preencher com zero contaminaria a distribuição com
        # referências que nunca poderiam ter o buff, distorcendo o grade
        # de quem o mantém de verdade. `presence` (acima, cohort inteiro)
        # continua controlando só SE o achado é relevante o bastante para
        # reportar (T3.1); a distribuição de comparação é outra decisão.
        comparison = comparisons[f"aura_uptime_fraction:{spell_id}"]
        if comparison.finding is None:
            continue
        findings.append(
            UptimeFinding(
                spell=catalog.get(spell_id),
                finding=comparison.finding,
                comparison=comparison,
            )
        )
    return tuple(findings)


def _build_waste_findings(
    player_log: PlayerLog, matched_logs: Sequence[PlayerLog]
) -> tuple[WasteFinding, ...]:
    findings: list[WasteFinding] = []
    for resource_type in sorted(player_log.resource_waste):
        ref_values = [rl.resource_waste.get(resource_type, 0.0) for rl in matched_logs]
        user_value = player_log.resource_waste[resource_type]
        findings.append(
            WasteFinding(
                resource_type=resource_type,
                finding=grade_scalar(user_value, ref_values, "lower_better"),
            )
        )
    return tuple(findings)


def analyze_performance_features(
    player_log: PlayerLog, matched_logs: Sequence[PlayerLog], catalog: SpellCatalog
) -> PerformanceFindings:
    active_time = None
    if player_log.active_time_pct is not None:
        ref_active = [rl.active_time_pct for rl in matched_logs if rl.active_time_pct is not None]
        active_time = grade_scalar(player_log.active_time_pct, ref_active, "higher_better")

    ref_deaths = [float(rl.deaths) for rl in matched_logs]
    deaths = grade_scalar(float(player_log.deaths), ref_deaths, "lower_better")

    ref_downtime = [rl.downtime_s for rl in matched_logs]
    downtime = grade_scalar(player_log.downtime_s, ref_downtime, "lower_better")

    return PerformanceFindings(
        active_time=active_time,
        deaths=deaths,
        downtime=downtime,
        uptimes=_build_uptime_findings(player_log, matched_logs, catalog),
        resource_waste=_build_waste_findings(player_log, matched_logs),
    )
