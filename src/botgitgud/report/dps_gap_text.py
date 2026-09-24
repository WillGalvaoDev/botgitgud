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
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _fmt_pp(value: float | None) -> str:
    if value is None:
        return "n/d"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}pp"


def render_dps_gap_section(report: DpsGapReport) -> list[str]:
    lines = ["", "💥 **DE ONDE VEIO O GAP DE DPS**", "-" * 42]
    if report.accounting_status in {"NO_REFERENCES", "INSUFFICIENT_REFERENCES"}:
        measured = (
            _fmt_dps_compact(report.measured_dps) if report.measured_dps is not None else "n/d"
        )
        return [
            *lines,
            f"Você: {measured} DPS | {report.accounting_status}",
            f"Comparação indisponível (N={report.reference_n_quantitative}; mínimo=8).",
        ]

    if not report.quantitative_damage_available:
        return [
            *lines,
            f"Comparacao indisponivel: {report.accounting_status}",
            ", ".join(report.comparison_reasons),
        ]
    cohort_value = report.reference_mean_dps if report.reference_n_quantitative else None
    cohort_fmt = _fmt_dps_compact(cohort_value) if cohort_value is not None else "n/d"
    measured_gap = (
        100 * report.total_delta_dps / report.reference_mean_dps
        if report.total_delta_dps is not None
        and report.reference_mean_dps is not None
        and report.reference_mean_dps > 0
        else None
    )
    gap_fmt = f"{measured_gap:+.1f}%" if measured_gap is not None else "n/d"
    player_fmt = _fmt_dps_compact(report.measured_dps) if report.measured_dps is not None else "n/d"
    lines.append(
        f"Você: {player_fmt} DPS | "
        f"Referências (média de DPS medido; N={report.reference_n_quantitative}): "
        f"{cohort_fmt} DPS | Gap observado: {gap_fmt}"
    )

    if not report.abilities and report.n_other == 0:
        if report.support_delta_dps:
            lines.append(f"Suporte: {_fmt_dps_compact(report.support_delta_dps)} DPS")
        if report.unclassified_dps:
            lines.append(f"Não classificado: {_fmt_dps_compact(report.unclassified_dps)} DPS")
        if report.total_delta_dps is not None:
            lines.append(f"Delta total: {_fmt_dps_compact(report.total_delta_dps)} DPS")
        return lines

    lines.append("")
    for a in report.abilities:
        label = (
            "excedente de saída observado (causa não identificada)"
            if a.delta_ability_dps > 0
            else DIAGNOSIS_LABELS[a.diagnosis]
        )
        confidence_note = " (confiança baixa)" if a.confidence == "baixa" else ""
        lines.append(
            f"**{a.spell.name}** — Delta: {_fmt_dps_compact(a.delta_ability_dps)} DPS | "
            f"Volume: {_fmt_dps_compact(a.volume_dps)} DPS | "
            f"Por evento: {_fmt_dps_compact(a.per_event_dps)} DPS | "
            f"Interação: {_fmt_dps_compact(a.interaction_dps)} DPS | "
            f"Não classificado: {_fmt_dps_compact(a.unclassified_dps)} DPS | "
            f"{label}{confidence_note}"
        )

    if report.n_other > 0:
        if report.other_pct is None:
            lines.append(
                f"(outras {report.n_other}) {_fmt_dps_compact(report.other_delta_dps)} DPS"
            )
        else:
            lines.append(
                f"(outras {report.n_other}) {_fmt_dps_compact(report.other_delta_dps)} DPS"
            )
    if report.support_delta_dps:
        lines.append(f"Suporte: {_fmt_dps_compact(report.support_delta_dps)} DPS")
    if report.total_delta_dps is not None:
        lines.append(f"Delta total: {_fmt_dps_compact(report.total_delta_dps)} DPS")

    return lines
