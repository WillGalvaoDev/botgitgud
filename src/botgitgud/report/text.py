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
from typing import TYPE_CHECKING

from botgitgud.analysis.cohort_match import ITEM_LEVEL_BAND, TIER_PIECES_BAND
from botgitgud.analysis.comparison import SpellComparison
from botgitgud.analysis.dps_gap import DpsGapReport
from botgitgud.analysis.findings import TopPriorities
from botgitgud.analysis.measurement import DamageComparison
from botgitgud.analysis.performance_features import PerformanceFindings
from botgitgud.analysis.proc_analysis import ProcAnalysis
from botgitgud.analysis.setup_analysis import SetupAnalysis
from botgitgud.domain.models import RunManifest
from botgitgud.report.cd_sections_text import render_cd_sections
from botgitgud.report.dps_gap_text import render_dps_gap_section
from botgitgud.report.performance_text import render_resource_efficiency_section
from botgitgud.report.setup_text import render_setup_section
from botgitgud.report.top_actions_text import render_top_actions_section

if TYPE_CHECKING:
    from botgitgud.analysis.pipeline import CoreAbilityReport, ExternalDpsContext
    from botgitgud.report.contract import ConfidenceSummary

_SEPARATOR = "=" * 42
_EMPTY_TOP_PRIORITIES = TopPriorities()

# T2.1: display labels for analysis/cohort_match.py's covariate names.
_COVARIATE_LABELS: dict[str, str] = {
    "tier_pieces": f"peças de tier ±{TIER_PIECES_BAND}",
    "external_buffs": "buffs externos",
    "item_level": f"ilvl ±{int(ITEM_LEVEL_BAND)}",
    "talent_cluster": "talentos: mesma build",
    "has_augmentation": "Augmentation",
}
_AUGMENTATION_RELAXED_WARNING = (
    "Buffs de suporte não pareados — parte da diferença observada "
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
    damage_comparison: DamageComparison | None = None
    matched_reference_n: int | None = None
    positional_reference_n: int | None = None
    damage_comparison_status: str = "UNKNOWN"


def _fmt_duration(seconds: float) -> str:
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


def _fmt_dps(value: float | None) -> str:
    return f"{value:,.0f}" if value is not None else "n/d"


def _fmt_percentile(value: float | None) -> str:
    if value is None:
        return "n/d"
    if value == 100.0:
        return "100"

    rendered = f"{value:.1f}"
    if value < 100.0 and rendered == "100.0":
        return "99.9"
    return rendered.removesuffix(".0")


def _render_covariates_line(header: ReportHeader) -> str | None:
    """T2.1: `Coorte: 43 logs | ilvl ±5 ✅ | talentos: mesma build ✅ |
    duração ±7% ✅` — declares which covariates were matched exactly.
    """
    if not header.matched_covariates:
        return None
    n = header.matched_reference_n if header.matched_reference_n is not None else header.reference_n
    parts = [f"**Coorte pareada:** {n} logs"]
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
    n = header.damage_comparison.reference_n if header.damage_comparison else header.reference_n
    status = header.damage_comparison_status
    if n < 8 and status == "AVAILABLE":
        status = "INSUFFICIENT_REFERENCES" if n else "NO_REFERENCES"
    median = header.cohort_median_dps if status == "AVAILABLE" and n >= 8 else None
    lines = [
        _SEPARATOR,
        "GITGUD MAJOR CD ANALYSIS",
        _SEPARATOR,
        f"**Player:** {header.char_name}",
        f"**Boss:** {header.boss_name}",
        f"**Spec:** {header.spec} {header.class_name}",
        (
            f"**DPS WCL:** {_fmt_dps(header.player_dps)} "
            f"(percentil: {_fmt_percentile(header.player_percentile)})"
        ),
        (
            f"**Referência:** {n} logs "
            f"| DPS medido mediano: {_fmt_dps(median)} "
            f"| Duração: {_fmt_duration(header.duration_min_s)} - "
            f"{_fmt_duration(header.duration_max_s)}"
        ),
    ]
    if status != "AVAILABLE":
        lines.append(f"Comparação medida: {status}")
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
    performance: PerformanceFindings | None = None,
    dps_gap: DpsGapReport | None = None,
    top_actions: TopPriorities = _EMPTY_TOP_PRIORITIES,
    setup: SetupAnalysis | None = None,
    confidence: ConfidenceSummary | None = None,
    core_abilities: Sequence[CoreAbilityReport] = (),
    proc_analysis: ProcAnalysis | None = None,
    external_dps_context: Sequence[ExternalDpsContext] = (),
) -> str:
    """T3.3's normative report structure: 1. Cabeçalho 2. Top 3 ações
    (`top_actions`) 3. De onde veio o gap de DPS (T3.2, `dps_gap`) 4. SETUP
    (RP.2, `setup` — observacional, nunca concorre por Top 3) 5.
    Detalhamento por categoria, na ordem da T3.1 (Mortes/downtime, Active
    time, Uptimes, Waste de recurso, Usos perdidos de CD, Timing de CD)
    6. Desvios menores (bundled into `render_cd_sections`, which already
    ends with that collapsed section) 7. Rodapé. Every section from Top 3
    onward renders even with zero CD comparisons — none of them are
    contingent on eligible cooldowns.

    EC.4: the "BUILD DIVERGENTE" section that used to render here was
    removed along with `analysis/talent_cluster.py`'s `BuildDivergence` —
    see that module's docstring for why. RP.2: `setup` (SA.6's
    `SetupAnalysis`) renders via `render_setup_section` (RP.1) in
    (approximately) the same slot BUILD DIVERGENTE used to occupy — the
    observational replacement the SA milestone was built for, never a
    revival of the removed causal finding. `setup=None` (the default)
    renders nothing, so every EXISTING call site that doesn't pass it is
    byte-for-byte unaffected.
    """
    lines: list[str] = _render_header(header)
    lines.extend(render_top_actions_section(top_actions))
    if dps_gap is not None:
        lines.extend(render_dps_gap_section(dps_gap))

    if core_abilities:
        lines.extend(["", "🧩 **3 ANALISE POR HABILIDADE**", "-" * 42])
        labels = {
            "damage_share": "dano",
            "cast_count": "usos",
            "casts_per_minute": "usos/min",
            "cast_timeline": "timeline observada",
            "uptime": "uptime",
        }
        for ability in core_abilities[:10]:
            features = ", ".join(labels[item.value] for item in ability.available_features)
            lines.append(f"**{ability.name}**: {features}")

    if proc_analysis is not None and proc_analysis.metrics:
        lines.extend(["", "✨ **4 SELF BUFFS & PROCS**", "-" * 42])
        for metric in proc_analysis.metrics:
            details = [f"{metric.procs} proc(s)"]
            if metric.uptime_frac is not None:
                details.append(f"uptime {metric.uptime_frac * 100:.1f}%")
            if metric.possibly_wasted_bands is not None:
                details.append(
                    f"janelas possivelmente desperdiçadas: {metric.possibly_wasted_bands}"
                )
            lines.append(f"**Spell {metric.spell_id}**: " + " | ".join(details))

    if performance is not None:
        lines.extend(render_resource_efficiency_section(performance))

    if comparisons:
        lines.extend(["", "⏱️ **6 TIMELINE OFENSIVA**", "-" * 42])
        lines.extend(render_cd_sections(comparisons))

    if external_dps_context:
        lines.extend(["", "🌐 **7 CONTEXTO DE DPS EXTERNO**", "-" * 42])
        lines.append("Estes efeitos vêm de fora e não representam a execução do jogador.")
        for item in external_dps_context:
            suffix = f": {item.damage:,.0f} dano" if item.damage > 0 else ""
            lines.append(f"**{item.name}**{suffix}")

    lines.extend(render_setup_section(setup))

    if confidence is not None and any(
        (
            confidence.reference_pool_members is not None,
            confidence.matched_cohort_members is not None,
            confidence.cohort_warnings,
            confidence.matched_covariates,
            confidence.relaxed_covariates,
        )
    ):
        lines.extend(["", "📊 **9 COORTE & CONFIANCA**", "-" * 42])
        if confidence.reference_pool_members is not None:
            lines.append(f"Pool de referência: {confidence.reference_pool_members} logs")
        if confidence.matched_cohort_members is not None:
            lines.append(f"Coorte pareada: {confidence.matched_cohort_members} logs")
        if confidence.matched_covariates:
            lines.append(f"Covariáveis pareadas: {', '.join(confidence.matched_covariates)}")
        if confidence.relaxed_covariates:
            lines.append(f"Covariáveis relaxadas: {', '.join(confidence.relaxed_covariates)}")
        lines.extend(f"Aviso: {warning}" for warning in confidence.cohort_warnings)

    lines.extend(_render_manifest_footer(manifest))
    lines.append("")
    lines.append(_SEPARATOR)
    return "\n".join(lines)


def render_header_and_top3(header: ReportHeader, top_actions: TopPriorities) -> str:
    """T3.4: "Discord passa a enviar: cabeçalho + Top 3 em texto, e o HTML
        como anexo" — the short text message that accompanies the HTML
    separate artifact, instead of the full report.
    """
    lines = list(_render_header(header))
    lines.extend(render_top_actions_section(top_actions))
    lines.append(_SEPARATOR)
    return "\n".join(lines)
