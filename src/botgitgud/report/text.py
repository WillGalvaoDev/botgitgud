"""T0.7 — text report renderer, replacing legacy/bot.py's
generate_coach_report_string.

Changes from the legacy renderer (docs/implementacao.md T0.7):
1. A "USOS PERDIDOS" section at the top lists every MISSED usage by
   ability — the achado 3.1 fix made visible in the report itself.
2. Each ability shows "Usos: <n_user> (coorte: <n_ref_mediana>)".
3. EXTRA steps show as "Uso extra" instead of a masked delta=0.0.
4. The header shows the player's own DPS and percentile (achado 3.11),
   sourced by the caller — never fabricated here.
5. The header shows the reference count (`n`) explicitly.
6/7. There is no cohort "average parse" field: characterRankings has no
     percentile field (docs/schema_confirmado.md §8), so legacy's
     "Parse méd: 99" was fiction in every report it ever produced
     (achado 3.10). The cohort's median DPS is shown instead.

T2.3: the old fixed 10s/25s MATCH delta thresholds are gone — grading is
now relative to each position's own empirical reference distribution
(analysis/grading.py, via report/grading_text.py), with a Benjamini-
Hochberg pass collapsing statistically-unreliable yellow/red findings into
their own section instead of showing them inline.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from botgitgud.analysis.cohort_match import ITEM_LEVEL_BAND, TIER_PIECES_BAND
from botgitgud.analysis.comparison import SpellComparison
from botgitgud.analysis.dps_gap import DpsGapReport
from botgitgud.analysis.performance_features import PerformanceFindings
from botgitgud.analysis.talent_cluster import BuildDivergence
from botgitgud.domain.models import RunManifest
from botgitgud.report.build_divergence_text import render_build_divergence
from botgitgud.report.cd_sections_text import render_cd_sections
from botgitgud.report.dps_gap_text import render_dps_gap_section
from botgitgud.report.performance_text import (
    render_active_time_section,
    render_deaths_downtime_section,
    render_resource_waste_section,
    render_uptimes_section,
)

_SEPARATOR = "=" * 42

# T2.1: display labels for analysis/cohort_match.py's covariate names.
_COVARIATE_LABELS: dict[str, str] = {
    "tier_pieces": f"peças de tier ±{TIER_PIECES_BAND}",
    "external_buffs": "buffs externos",
    "item_level": f"ilvl ±{int(ITEM_LEVEL_BAND)}",
    "talent_cluster": "talentos: mesma build",
    "has_augmentation": "Augmentation",
}
_AUGMENTATION_RELAXED_WARNING = (
    "Buffs de suporte não pareados — parte do gap de dano por cast "
    "pode não ser controlável por você."
)


@dataclass(frozen=True, slots=True)
class ReportHeader:
    char_name: str
    boss_name: str
    class_name: str
    spec: str
    reference_n: int
    duration_min_s: float
    duration_max_s: float
    player_dps: float | None = None
    player_percentile: float | None = None
    cohort_median_dps: float | None = None
    cohort_warnings: tuple[str, ...] = ()
    matched_covariates: tuple[str, ...] = ()  # T2.1: analysis.cohort_match.MatchReport.matched
    relaxed_covariates: tuple[str, ...] = ()  # T2.1: ...MatchReport.relaxed


def _fmt_duration(seconds: float) -> str:
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


def _fmt_dps(value: float | None) -> str:
    return f"{value:,.0f}" if value is not None else "n/d"


def _fmt_percentile(value: float | None) -> str:
    return f"{value:.0f}" if value is not None else "n/d"


def _render_covariates_line(header: ReportHeader) -> str | None:
    """T2.1: `Coorte: 43 logs | ilvl ±5 ✅ | talentos: mesma build ✅ |
    duração ±7% ✅` — declares which covariates were matched exactly.
    """
    if not header.matched_covariates:
        return None
    parts = [f"**Coorte:** {header.reference_n} logs"]
    for cov in header.matched_covariates:
        if cov.startswith("duration"):
            parts.append(f"duração {cov.removeprefix('duration')} ✅")
        else:
            parts.append(f"{_COVARIATE_LABELS.get(cov, cov)} ✅")
    return " | ".join(parts)


def _render_relaxed_covariate_warnings(header: ReportHeader) -> list[str]:
    """T2.1: one ⚠️ line per relaxed covariate — `has_augmentation` gets
    the specific support-buff warning the spec mandates; every other
    relaxed covariate gets the generic `<label> não pareado` line.
    """
    lines: list[str] = []
    for cov in header.relaxed_covariates:
        if cov == "has_augmentation":
            lines.append(f"⚠️ {_AUGMENTATION_RELAXED_WARNING}")
        else:
            label = _COVARIATE_LABELS.get(cov, cov)
            lines.append(f"⚠️ {label} não pareado (amostra insuficiente)")
    return lines


def _render_header(header: ReportHeader) -> list[str]:
    lines = [
        _SEPARATOR,
        "GITGUD MAJOR CD ANALYSIS",
        _SEPARATOR,
        f"**Player:** {header.char_name}",
        f"**Boss:** {header.boss_name}",
        f"**Spec:** {header.spec} {header.class_name}",
        (
            f"**DPS:** {_fmt_dps(header.player_dps)} "
            f"(percentil: {_fmt_percentile(header.player_percentile)})"
        ),
        (
            f"**Referência:** {header.reference_n} logs "
            f"| DPS mediano: {_fmt_dps(header.cohort_median_dps)} "
            f"| Duração: {_fmt_duration(header.duration_min_s)} - "
            f"{_fmt_duration(header.duration_max_s)}"
        ),
    ]
    covariates_line = _render_covariates_line(header)
    if covariates_line is not None:
        lines.append(covariates_line)
    lines.extend(_render_relaxed_covariate_warnings(header))
    for warning in header.cohort_warnings:
        lines.append(f"⚠️ {warning}")
    lines.append(_SEPARATOR)
    return lines


def _render_manifest_footer(manifest: RunManifest | None) -> list[str]:
    if manifest is None:
        return []
    return [
        "",
        (
            f"_Cohort: {manifest.cohort_id} | Versão: {manifest.code_version} "
            f"| Gerado: {manifest.generated_at.isoformat()}_"
        ),
    ]


def render_report(
    header: ReportHeader,
    comparisons: Sequence[SpellComparison],
    manifest: RunManifest | None = None,
    build_divergence: BuildDivergence | None = None,
    performance: PerformanceFindings | None = None,
    dps_gap: DpsGapReport | None = None,
) -> str:
    """Section order (interim, pre-T3.3 — the final normative order, Top 3
    then this same content, is T3.3's own job): 1. Build (`build_divergence`,
    above the header) 2. De onde veio o gap de DPS (T3.2, `dps_gap`) 3.
    Mortes/downtime 4. Active time 5. Uptimes 6. Waste de recurso 7. Usos
    perdidos de CD 8. Timing de CD (`render_cd_sections`). Every section
    from `dps_gap` onward renders even with zero CD comparisons — none of
    them are contingent on eligible cooldowns.
    """
    lines: list[str] = []
    if build_divergence is not None:
        lines.extend(render_build_divergence(build_divergence))
        lines.append("")
    lines.extend(_render_header(header))

    if dps_gap is not None:
        lines.extend(render_dps_gap_section(dps_gap))
    if performance is not None:
        lines.extend(render_deaths_downtime_section(performance.deaths, performance.downtime))
        lines.extend(render_active_time_section(performance.active_time))
        lines.extend(render_uptimes_section(performance.uptimes))
        lines.extend(render_resource_waste_section(performance.resource_waste))

    if not comparisons:
        lines.append("")
        lines.append("⚡ Nenhum Major/Minor CD elegível encontrado.")
        lines.extend(_render_manifest_footer(manifest))
        lines.append(_SEPARATOR)
        return "\n".join(lines)

    lines.extend(render_cd_sections(comparisons))
    lines.extend(_render_manifest_footer(manifest))
    lines.append("")
    lines.append(_SEPARATOR)
    return "\n".join(lines)


def chunk_report_for_discord(text: str, max_len: int = 1900) -> list[str]:
    """Break `text` on line boundaries into chunks of at most `max_len`
    characters, never mid-line (achado 4.8 — legacy's blind 1900-char slice
    could split a line, and with it a markdown code fence, in half).
    """
    lines = text.split("\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in lines:
        added_len = len(line) + (1 if current else 0)  # + newline joining it
        if current and current_len + added_len > max_len:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
            added_len = len(line)
        current.append(line)
        current_len += added_len

    if current:
        chunks.append("\n".join(current))

    return chunks
