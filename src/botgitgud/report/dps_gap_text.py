"""T3.2 — renders analysis/dps_gap.py's DpsGapReport as its own report
section (split out of report/text.py, T1.6's 300-line limit).
"""

from __future__ import annotations

from botgitgud.analysis.dps_gap import DIAGNOSIS_LABELS, DpsGapReport


def _fmt_dps_compact(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f}k"
    return f"{value:.0f}"


def _fmt_pp(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}pp"


def render_dps_gap_section(report: DpsGapReport) -> list[str]:
    lines = ["", "💥 **DE ONDE VEIO O GAP DE DPS**", "-" * 42]

    cohort_fmt = _fmt_dps_compact(report.cohort_median_dps) if report.cohort_median_dps else "n/d"
    gap_fmt = f"{report.gap_pct * 100:+.1f}%" if report.gap_pct is not None else "n/d"
    lines.append(
        f"Você: {_fmt_dps_compact(report.player_dps)} DPS | "
        f"Coorte (mediana): {cohort_fmt} DPS | Gap: {gap_fmt}"
    )

    if not report.abilities and report.n_other == 0:
        return lines

    lines.append("")
    for a in report.abilities:
        label = DIAGNOSIS_LABELS[a.diagnosis]
        confidence_note = " (confiança baixa)" if a.confidence == "baixa" else ""
        lines.append(
            f"**{a.spell.name}** — Gap: {_fmt_pp(a.delta_dps_pct)} | "
            f"Volume: {_fmt_pp(a.volume_dps_pct)} | Eficiência: {_fmt_pp(a.efficiency_dps_pct)} | "
            f"{label}{confidence_note}"
        )

    if report.n_other > 0:
        lines.append(f"(outras {report.n_other}) {_fmt_pp(report.other_pct)}")

    return lines
