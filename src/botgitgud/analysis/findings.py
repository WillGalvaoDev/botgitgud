"""Observed execution findings and entity-gated uptime candidates.

M1 reports observed deficits with named units and metric-specific populations.
These quantities do not estimate recoverable damage or identify a rotation error.
The legacy gain field is absent from the active contract. Death, activity and
resource waste retain their independently measured execution evidence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from botgitgud.analysis.dps_gap import DIAGNOSIS_LABELS, AbilityGap, DpsGapReport
from botgitgud.analysis.grading import FDR, benjamini_hochberg, two_tailed_p_value
from botgitgud.analysis.performance_features import PerformanceFindings, ScalarFinding

FindingKind = Literal[
    "DEATH",
    "ACTIVE_TIME",
    "UPTIME",
    "WASTE",
    "MISSED_CD",
    "CD_TIMING",
    "ABILITY_GAP",
]
Confidence = Literal["alta", "média", "baixa"]

# RB-1/RB-1a/RB-2: the three execution categories this unit connects, and
# the (grade) gate that decides which of them are material enough to enter
# the candidate set — green (fine) and insufficient (no comparison base)
# never do.
ExecutionCategory = Literal["DEATH", "ACTIVE_TIME", "WASTE"]
_MATERIAL_GRADES = frozenset({"red", "yellow"})

# Within-kind ordering uses the observed deficit and declared confidence.
CONFIDENCE_WEIGHT: dict[Confidence, float] = {"alta": 1.0, "média": 0.6, "baixa": 0.3}


@dataclass(frozen=True, slots=True)
class Finding:
    kind: FindingKind
    title: str
    detail: str

    confidence: Confidence
    evidence: Mapping[str, Any] = field(default_factory=dict)
    observed_deficit_player_pp: float | None = None

    @property
    def score(self) -> float | None:
        value = self.observed_deficit_player_pp
        if value is None:
            return None
        return abs(value) * CONFIDENCE_WEIGHT[self.confidence]


@dataclass(frozen=True, slots=True)
class RelevanceFinding:
    kind: FindingKind
    title: str
    detail: str
    offensive_relevance: float
    severity: float
    confidence: Confidence
    evidence: Mapping[str, Any] = field(default_factory=dict)

    @property
    def score(self) -> float:
        return self.offensive_relevance * self.severity * CONFIDENCE_WEIGHT[self.confidence]


@dataclass(frozen=True, slots=True)
class ExecutionFinding:
    """RB-1: execution evidence, carrying only what was measured — the
    category, the backing `ScalarFinding` verbatim (grade/quantile/
    user_value/direction, untransformed), and a subject when one exists
    (the resource type, for WASTE). Deliberately has no
    `estimated_gain_pct`, no `offensive_relevance`, and no cross-kind
    score — inventing any of those is the thing RB-1 forbids; ordering
    these against ABILITY_GAP/UPTIME is M31's decision, not this one's.
    """

    category: ExecutionCategory
    finding: ScalarFinding
    subject: str | None = None


@dataclass(frozen=True, slots=True)
class TopPriorities:
    level1: tuple[Finding, ...] = ()
    level2: tuple[RelevanceFinding, ...] = ()


def compute_confidence(
    *,
    n: int,
    any_covariate_relaxed: bool,
    feature_incomplete: bool = False,
    survives_bh: bool = True,
) -> Confidence:
    """M10's explicit confidence bands and overriding evidence gates.

    Samples below 15, incomplete features, and failure to survive BH are
    always low-confidence.  From 30 through 59, relaxation caps confidence
    at medium; at 60 or more the sample size supports high confidence even
    when a covariate was relaxed.
    """
    if n < 15 or feature_incomplete or not survives_bh:
        return "baixa"
    if n < 30:
        return "média"
    if n < 60 and any_covariate_relaxed:
        return "média"
    return "alta"


def _build_execution_findings(performance: PerformanceFindings | None) -> list[ExecutionFinding]:
    """M27/RB-1: connects `performance_features.py`'s already-graded
    scalars into execution candidates. RB-2: only `red`/`yellow` enter.

    RB-1a: ACTIVE_TIME emits at MOST one candidate, never two, even though
    both `active_time` and `downtime` measure the same fact — the direct
    measure (`active_time_pct`) backs it whenever this log has one; the
    `downtime` scalar is the fallback only when `active_time_pct` itself
    was never computed for this log (`performance.active_time is None`),
    never a second, additional candidate. DEATH is graded and appended
    independently of that choice — a death and the time it costs are two
    different remediations (RB-1a), not one collapsed metric.
    """
    if performance is None:
        return []

    execution: list[ExecutionFinding] = []

    if performance.deaths.grade in _MATERIAL_GRADES:
        execution.append(ExecutionFinding(category="DEATH", finding=performance.deaths))

    if performance.active_time is not None and performance.active_time.grade in _MATERIAL_GRADES:
        execution.append(ExecutionFinding(category="ACTIVE_TIME", finding=performance.active_time))

    for waste in performance.resource_waste:
        if waste.finding.grade in _MATERIAL_GRADES:
            execution.append(
                ExecutionFinding(
                    category="WASTE", finding=waste.finding, subject=waste.resource_type
                )
            )

    return execution


def build_findings(
    *,
    dps_gap: DpsGapReport,
    n: int,
    relaxed_covariates: Sequence[str],
    performance: PerformanceFindings | None,
    player_damage_share: Mapping[int, float],
) -> tuple[list[Finding], list[RelevanceFinding], list[ExecutionFinding]]:
    """Emit only eligible entities; preserve each scalar's own sample size."""
    any_relaxed = bool(relaxed_covariates)
    findings: list[Finding] = []

    abilities = dps_gap.abilities if dps_gap.quantitative_damage_available else ()
    for ability in abilities:
        if not ability.review_eligible or (
            ability.delta_dps_pct is not None and ability.delta_dps_pct >= 0
        ):
            continue  # already at or above the cohort on this ability — nothing to gain
        confidence: Confidence = (
            "baixa"
            if ability.confidence == "baixa"
            else compute_confidence(
                n=ability.gross_dps_finding.stats.n if ability.gross_dps_finding else n,
                any_covariate_relaxed=any_relaxed,
            )
        )
        findings.append(
            Finding(
                kind="ABILITY_GAP",
                title=f"{ability.spell.name}: {DIAGNOSIS_LABELS[ability.diagnosis]}",
                detail=(
                    f"O DPS observado de {ability.spell.name} ficou abaixo da referência: "
                    f"{ability.player_ability_dps:.3f} DPS contra "
                    f"{ability.reference_mean_ability_dps:.3f} DPS (média)."
                ),
                observed_deficit_player_pp=(
                    -ability.delta_dps_pct if ability.delta_dps_pct is not None else None
                ),
                confidence=confidence,
                evidence={
                    "spell_id": ability.spell.spell_id,
                    "volume_dps_pct": ability.volume_dps_pct,
                    "efficiency_dps_pct": ability.efficiency_dps_pct,
                },
            )
        )

    candidates: list[RelevanceFinding] = []
    p_values: list[float] = []
    if performance is not None:
        for uptime in performance.uptimes:
            if uptime.spell.spell_id not in dps_gap.entity_review_eligible:
                continue
            if uptime.finding.grade not in _MATERIAL_GRADES:
                continue
            quantile = uptime.finding.quantile
            if quantile is None:
                continue
            severity = abs(quantile - 0.5) * 2
            if severity < 0.5:
                continue
            offensive_relevance = player_damage_share.get(uptime.spell.spell_id)
            if offensive_relevance is None or not 0 < offensive_relevance <= 1:
                continue
            candidates.append(
                RelevanceFinding(
                    kind="UPTIME",
                    title=f"{uptime.spell.name}: uptime fora do esperado",
                    detail="Ajuste o uptime desta habilidade em relação à coorte comparável.",
                    offensive_relevance=offensive_relevance,
                    severity=severity,
                    confidence=compute_confidence(
                        n=uptime.finding.stats.n, any_covariate_relaxed=any_relaxed
                    ),
                    evidence={"quantile": quantile, "spell_id": uptime.spell.spell_id},
                )
            )
            p_values.append(two_tailed_p_value(quantile))

    survives = benjamini_hochberg(p_values, fdr=FDR)
    relevance = [candidate for candidate, keep in zip(candidates, survives, strict=True) if keep]
    execution_findings = _build_execution_findings(performance)
    return findings, relevance, execution_findings


def pair_ability_findings(
    report: DpsGapReport, findings: Sequence[Finding]
) -> list[tuple[Finding, AbilityGap]]:
    """Resolve the emitted identity; eligibility never changes positional pairing."""
    abilities = {a.spell.spell_id: a for a in report.abilities}
    return [(finding, abilities[finding.evidence["spell_id"]]) for finding in findings]


def select_top_priorities(
    findings: Sequence[Finding],
    relevance_findings: Sequence[RelevanceFinding],
    *,
    top_n: int = 3,
) -> TopPriorities:
    limit = max(top_n, 0)
    level1_candidates = [f for f in findings if f.observed_deficit_player_pp is not None]
    level1_candidates.sort(key=lambda f: (-(f.score or 0.0), f.title))
    level1 = tuple(level1_candidates[:limit])
    remaining = limit - len(level1)
    level2_candidates = sorted(relevance_findings, key=lambda f: (-f.score, f.title))
    return TopPriorities(level1=level1, level2=tuple(level2_candidates[:remaining]))


def select_top_actions(findings: Sequence[Finding], *, top_n: int = 3) -> list[Finding]:
    """T3.3 acceptance: 1-3 items, 0 only when no finding clears the gate
    (`observed_deficit_player_pp is not None`) — never a partially-scored finding.
    """
    return list(select_top_priorities(findings, (), top_n=top_n).level1)
