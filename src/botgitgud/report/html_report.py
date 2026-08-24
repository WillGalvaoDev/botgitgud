"""T3.4 — self-contained HTML report (docs/implementacao.md T3.4): "o
relatório completo já não cabe em texto." No CDN, no external resource
(every `<style>` is inline, every chart is server-rendered inline SVG —
see report/svg_charts.py), and the whole document must parse under a
strict XML parser (the acceptance criterion itself) — every tag is
explicitly closed, every attribute quoted, every text/attribute value
escaped via `html.escape`.

Accessibility (T3.4's own requirement): no 🟢/🟡/🔴 without an
accompanying text label — the feature table always renders both the
emoji AND its Portuguese label side by side; the charts add a legend
(svg_charts.render_grade_legend_svg) plus per-marker `<title>` tooltips.
"""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

from botgitgud.analysis.comparison import SpellComparison
from botgitgud.analysis.dps_gap import DIAGNOSIS_LABELS, DpsGapReport
from botgitgud.analysis.findings import Finding
from botgitgud.analysis.grading import Grade
from botgitgud.analysis.performance_features import PerformanceFindings, ScalarFinding
from botgitgud.analysis.talent_cluster import BuildDivergence
from botgitgud.domain.models import RunManifest
from botgitgud.report.svg_charts import (
    render_ability_timeline_svg,
    render_dps_gap_waterfall_svg,
    render_grade_legend_svg,
)
from botgitgud.report.text import ReportHeader

_GRADE_EMOJI: dict[Grade, str] = {
    "green": "🟢",
    "yellow": "🟡",
    "red": "🔴",
    "insufficient": "⚪",
}
_GRADE_TEXT: dict[Grade, str] = {
    "green": "dentro do esperado",
    "yellow": "desvio moderado",
    "red": "desvio significativo",
    "insufficient": "amostra insuficiente",
}

_CSS = """
body { font-family: -apple-system, Segoe UI, Arial, sans-serif; margin: 0;
  padding: 24px; background: #101418; color: #e6e6e6; }
h1, h2 { color: #ffffff; }
table { border-collapse: collapse; width: 100%; margin: 12px 0 24px 0; }
th, td { border: 1px solid #333333; padding: 6px 10px; text-align: left; font-size: 13px; }
th { background: #1c2128; }
.section { margin-bottom: 32px; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px;
  background: #1c2128; font-size: 12px; margin-right: 6px; }
.chart-row { margin-bottom: 10px; }
.chart-title { font-weight: bold; margin-bottom: 2px; }
"""


def _fmt_pct(value: float | None) -> str:
    return f"{value * 100:.1f}%" if value is not None else "n/d"


def _fmt_num(value: float | None) -> str:
    return f"{value:.1f}" if value is not None else "n/d"


def _grade_cell(finding: ScalarFinding) -> str:
    emoji = _GRADE_EMOJI[finding.grade]
    label = _GRADE_TEXT[finding.grade]
    return f"{emoji} {escape(label)}"


def _feature_row(name: str, finding: ScalarFinding, *, as_pct: bool) -> str:
    fmt = _fmt_pct if as_pct else _fmt_num
    percentile = f"{finding.quantile * 100:.0f}º" if finding.quantile is not None else "n/d"
    return (
        "<tr>"
        f"<td>{escape(name)}</td>"
        f"<td>{fmt(finding.user_value)}</td>"
        f"<td>{fmt(finding.stats.p50)}</td>"
        f"<td>{percentile}</td>"
        f"<td>{_grade_cell(finding)}</td>"
        "</tr>"
    )


def _render_feature_table(performance: PerformanceFindings) -> str:
    rows: list[str] = []
    if performance.active_time is not None:
        rows.append(_feature_row("Tempo ativo", performance.active_time, as_pct=True))
    rows.append(_feature_row("Mortes", performance.deaths, as_pct=False))
    rows.append(_feature_row("Downtime (s)", performance.downtime, as_pct=False))
    for uf in performance.uptimes:
        rows.append(_feature_row(f"Uptime: {uf.spell.name}", uf.finding, as_pct=True))
    for wf in performance.resource_waste:
        rows.append(_feature_row(f"Waste: {wf.resource_type}", wf.finding, as_pct=False))

    return (
        "<table>"
        "<tr><th>Feature</th><th>Você</th><th>Mediana coorte</th>"
        "<th>Percentil</th><th>Status</th></tr>" + "".join(rows) + "</table>"
    )


def _render_timelines(comparisons: Sequence[SpellComparison], duration_s: float) -> str:
    blocks: list[str] = []
    for c in comparisons:
        if not c.alignment.steps:
            continue
        blocks.append(
            '<div class="chart-row">'
            f'<div class="chart-title">{escape(c.spell.name)}</div>'
            f"{render_ability_timeline_svg(c, duration_s)}"
            "</div>"
        )
    return "".join(blocks)


def _render_top_actions(top_actions: Sequence[Finding]) -> str:
    scope = (
        "<p>Este ranking inclui somente achados com ganho de DPS quantificável; "
        "outros problemas podem aparecer nas seções abaixo.</p>"
    )
    if not top_actions:
        return scope + (
            "<p>Nenhum achado com ganho de DPS quantificável. "
            "Veja as seções abaixo para achados sem estimativa de ganho.</p>"
        )
    items = "".join(
        "<li>"
        f"<strong>{escape(f.title)}</strong> — ganho estimado: "
        f"{f.estimated_gain_pct:+.1f}pp "
        f'<span class="badge">confiança: {escape(f.confidence)}</span>'
        f"<br/>{escape(f.detail)}"
        "</li>"
        for f in top_actions
    )
    return f"{scope}<ol>{items}</ol>"


def render_html_report(
    header: ReportHeader,
    comparisons: Sequence[SpellComparison],
    *,
    manifest: RunManifest | None = None,
    build_divergence: BuildDivergence | None = None,
    performance: PerformanceFindings | None = None,
    dps_gap: DpsGapReport | None = None,
    top_actions: Sequence[Finding] = (),
    duration_s: float = 0.0,
) -> str:
    title = f"Análise de {header.char_name} — {header.boss_name}"
    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<html xmlns="http://www.w3.org/1999/xhtml" lang="pt-BR">',
        "<head>",
        '<meta charset="UTF-8"/>',
        f"<title>{escape(title)}</title>",
        f"<style>{_CSS}</style>",
        "</head>",
        "<body>",
        f"<h1>{escape(title)}</h1>",
        (
            f"<p>{escape(header.spec)} {escape(header.class_name)} | "
            f"DPS: {header.player_dps or 0:,.0f} | "
            f"Percentil: {_fmt_num(header.player_percentile)} | "
            f"Coorte: {header.reference_n} logs</p>"
        ),
        '<div class="section">',
        "<h2>🎯 Top 3 Ações com Ganho de DPS Quantificável</h2>",
        _render_top_actions(top_actions),
        "</div>",
    ]

    if build_divergence is not None:
        parts.append('<div class="section">')
        parts.append("<h2>🧬 Build Divergente</h2>")
        parts.append(
            f"<p>Sua build aparece em {build_divergence.player_pct * 100:.0f}% dos top "
            f"parses ({build_divergence.player_cluster_n}/{build_divergence.total_n}).</p>"
        )
        parts.append("</div>")

    if dps_gap is not None:
        parts.append('<div class="section">')
        parts.append("<h2>💥 De Onde Veio o Gap de DPS</h2>")
        gap_fmt = f"{dps_gap.gap_pct * 100:+.1f}%" if dps_gap.gap_pct is not None else "n/d"
        parts.append(f"<p>Você: {dps_gap.player_dps:,.0f} DPS | Gap vs. coorte: {gap_fmt}</p>")
        parts.append(render_dps_gap_waterfall_svg(dps_gap))
        rows = "".join(
            "<tr>"
            f"<td>{escape(a.spell.name)}</td>"
            f"<td>{a.delta_dps_pct:+.1f}pp</td>"
            f"<td>{a.volume_dps_pct:+.1f}pp</td>"
            f"<td>{a.efficiency_dps_pct:+.1f}pp</td>"
            f"<td>{escape(DIAGNOSIS_LABELS[a.diagnosis])}</td>"
            "</tr>"
            for a in dps_gap.abilities
        )
        parts.append(
            "<table><tr><th>Habilidade</th><th>Gap</th><th>Volume</th>"
            f"<th>Eficiência</th><th>Diagnóstico</th></tr>{rows}</table>"
        )
        parts.append("</div>")

    if performance is not None:
        parts.append('<div class="section">')
        parts.append("<h2>📋 Tabela de Features</h2>")
        parts.append(render_grade_legend_svg())
        parts.append(_render_feature_table(performance))
        parts.append("</div>")

    if comparisons:
        parts.append('<div class="section">')
        parts.append("<h2>⏱ Timeline por Habilidade</h2>")
        parts.append(render_grade_legend_svg())
        parts.append(_render_timelines(comparisons, duration_s))
        parts.append("</div>")

    if manifest is not None:
        parts.append(
            f"<p><small>Cohort: {escape(manifest.cohort_id)} | "
            f"Versão: {escape(manifest.code_version)} | "
            f"Gerado: {escape(manifest.generated_at.isoformat())}</small></p>"
        )

    parts.append("</body>")
    parts.append("</html>")
    return "".join(parts)
