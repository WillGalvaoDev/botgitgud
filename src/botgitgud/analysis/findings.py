"""T3.3 — the unified `Finding` type, its priority score, and Top 3
selection (docs/implementacao.md T3.3, "recomendação 6.6").

docs/desvios.md D-31: only BUILD (T2.2) and ABILITY_GAP (T3.2) findings
get a real `estimated_gain_pct` here — the document defines no formula
for translating DEATH/ACTIVE_TIME/UPTIME/WASTE/MISSED_CD/CD_TIMING into a
DPS-percentage gain, and this project's own established ethos (D-28: "não
fabricar dados") refuses to invent a linear-scaling guess and present it
as if it were a real quantity. Those categories still render their own
section (report/text.py's "detalhamento por categoria", unchanged from
T3.1) with no gain estimate — they are correctly excluded from Top 3
(`estimated_gain_pct=None` findings never compete, per the document's own
acceptance criterion), not silently dropped.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from botgitgud.analysis.dps_gap import DIAGNOSIS_LABELS, DpsGapReport
from botgitgud.analysis.talent_cluster import BuildDivergence

FindingKind = Literal[
    "BUILD",
    "DEATH",
    "ACTIVE_TIME",
    "UPTIME",
    "WASTE",
    "MISSED_CD",
    "CD_TIMING",
    "ABILITY_GAP",
]
Confidence = Literal["alta", "média", "baixa"]

# T3.3: "Score de prioridade: estimated_gain_pct x peso_de_confiança".
CONFIDENCE_WEIGHT: dict[Confidence, float] = {"alta": 1.0, "média": 0.6, "baixa": 0.3}


@dataclass(frozen=True, slots=True)
class Finding:
    kind: FindingKind
    title: str
    detail: str
    estimated_gain_pct: float | None
    confidence: Confidence
    evidence: Mapping[str, Any] = field(default_factory=dict)

    @property
    def score(self) -> float | None:
        if self.estimated_gain_pct is None:
            return None
        return abs(self.estimated_gain_pct) * CONFIDENCE_WEIGHT[self.confidence]


def compute_confidence(
    *,
    n: int,
    any_covariate_relaxed: bool,
    feature_incomplete: bool = False,
    survives_bh: bool = True,
) -> Confidence:
    """T3.3's 3-tier rule: `alta` needs n>=30 AND no relaxed covariate AND
    (where applicable) BH survival — any single failure among those three
    caps it at `média`, except the two conditions the document lists
    explicitly under `baixa` (n<15, or the feature itself is derived from
    incomplete data), which override everything else.
    """
    if n < 15 or feature_incomplete:
        return "baixa"
    if any_covariate_relaxed or n < 30 or not survives_bh:
        return "média"
    return "alta"


def build_findings(
    *,
    build_divergence: BuildDivergence | None,
    dps_gap: DpsGapReport,
    n: int,
    relaxed_covariates: Sequence[str],
) -> list[Finding]:
    """Every Finding this project can currently back with a real
    `estimated_gain_pct` — see the module docstring (D-31) for why DEATH/
    ACTIVE_TIME/UPTIME/WASTE/MISSED_CD/CD_TIMING aren't represented here.
    """
    any_relaxed = bool(relaxed_covariates)
    findings: list[Finding] = []

    if build_divergence is not None:
        findings.append(
            Finding(
                kind="BUILD",
                title="Build em cluster minoritário da coorte",
                detail=(
                    f"Sua build aparece em {build_divergence.player_pct * 100:.0f}% dos top "
                    f"parses ({build_divergence.player_cluster_n}/{build_divergence.total_n})."
                ),
                estimated_gain_pct=build_divergence.estimated_gain_pct,
                confidence=compute_confidence(n=n, any_covariate_relaxed=any_relaxed),
                evidence={"differences": build_divergence.differences},
            )
        )

    for ability in dps_gap.abilities:
        if ability.delta_dps_pct >= 0:
            continue  # already at or above the cohort on this ability — nothing to gain
        confidence: Confidence = (
            "baixa"
            if ability.confidence == "baixa"
            else compute_confidence(n=n, any_covariate_relaxed=any_relaxed)
        )
        findings.append(
            Finding(
                kind="ABILITY_GAP",
                title=f"{ability.spell.name}: {DIAGNOSIS_LABELS[ability.diagnosis]}",
                detail=f"Gap de {ability.delta_dps_pct:+.1f}pp do seu DPS total nesta habilidade.",
                estimated_gain_pct=-ability.delta_dps_pct,
                confidence=confidence,
                evidence={
                    "volume_dps_pct": ability.volume_dps_pct,
                    "efficiency_dps_pct": ability.efficiency_dps_pct,
                },
            )
        )

    return findings


def select_top_actions(findings: Sequence[Finding], *, top_n: int = 3) -> list[Finding]:
    """T3.3 acceptance: 1-3 items, 0 only when no finding clears the gate
    (`estimated_gain_pct is not None`) — never a partially-scored finding.
    """
    scored = [f for f in findings if f.estimated_gain_pct is not None]
    scored.sort(key=lambda f: f.score or 0.0, reverse=True)
    return scored[:top_n]
