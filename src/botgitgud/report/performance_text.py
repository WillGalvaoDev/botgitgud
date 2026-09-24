"""T3.1 split of report/text.py's own growth — rendering for the
performance features beyond casts (docs/implementacao.md T3.1's
normative report order, items 2/3/4/5: deaths/downtime, active time,
uptimes, resource waste — all BEFORE usos perdidos/timing, items 6/7).
"""

from __future__ import annotations

from collections.abc import Sequence

from botgitgud.analysis.performance_features import (
    PerformanceFindings,
    ScalarFinding,
    UptimeFinding,
    WasteFinding,
)

_GRADE_EMOJI: dict[str, str] = {"green": "🟢", "yellow": "🟡", "red": "🔴"}


def _status(finding: ScalarFinding) -> str:
    if finding.grade == "insufficient":
        return f"⚪ amostra insuficiente (n={finding.stats.n})"
    return _GRADE_EMOJI[finding.grade]


def render_deaths_downtime_section(deaths: ScalarFinding, downtime: ScalarFinding) -> list[str]:
    lines = ["", "💀 **MORTES E DOWNTIME**", "-" * 42]
    d_line = f"Mortes: {int(deaths.user_value)}"
    if deaths.stats.p50 is not None:
        d_line += f" (coorte mediana: {deaths.stats.p50:.1f})"
    lines.append(f"{d_line} {_status(deaths)}")

    dt_line = f"Downtime: {downtime.user_value:.1f}s"
    if downtime.stats.p50 is not None:
        dt_line += f" (coorte mediana: {downtime.stats.p50:.1f}s)"
    lines.append(f"{dt_line} {_status(downtime)}")
    return lines


def render_active_time_section(finding: ScalarFinding | None) -> list[str]:
    if finding is None:
        return []
    lines = ["", "🏃 **ACTIVE TIME**", "-" * 42]
    line = f"Tempo ativo: {finding.user_value * 100:.1f}%"
    if finding.stats.p50 is not None:
        line += f" (coorte mediana: {finding.stats.p50 * 100:.1f}%)"
    lines.append(f"{line} {_status(finding)}")
    return lines


def render_uptimes_section(findings: Sequence[UptimeFinding]) -> list[str]:
    if not findings:
        return []
    lines = ["", "🔰 **UPTIMES**", "-" * 42]
    for uf in findings:
        line = f"**{uf.spell.name}**: {uf.finding.user_value * 100:.1f}%"
        if uf.finding.stats.n >= 8 and uf.finding.stats.p50 is not None:
            line += f" (coorte mediana: {uf.finding.stats.p50 * 100:.1f}%)"
        lines.append(f"{line} {_status(uf.finding)}")
    return lines


def render_resource_waste_section(findings: Sequence[WasteFinding]) -> list[str]:
    if not findings:
        return []
    lines = ["", "♻️ **WASTE DE RECURSO**", "-" * 42]
    for wf in findings:
        line = f"**{wf.resource_type}**: {wf.finding.user_value:.0f}"
        if wf.finding.stats.p50 is not None:
            line += f" (coorte mediana: {wf.finding.stats.p50:.0f})"
        lines.append(f"{line} {_status(wf.finding)}")
    return lines


def render_resource_efficiency_section(performance: PerformanceFindings) -> list[str]:
    """Render M12 section 5 as one cohesive, non-empty section."""
    lines = ["", "📈 **5 EFICIENCIA DE RECURSO**", "-" * 42]

    deaths = performance.deaths
    death_line = f"Mortes: {int(deaths.user_value)}"
    if deaths.stats.p50 is not None:
        death_line += f" (coorte mediana: {deaths.stats.p50:.1f})"
    lines.append(f"{death_line} {_status(deaths)}")

    downtime = performance.downtime
    downtime_line = f"Downtime: {downtime.user_value:.1f}s"
    if downtime.stats.p50 is not None:
        downtime_line += f" (coorte mediana: {downtime.stats.p50:.1f}s)"
    lines.append(f"{downtime_line} {_status(downtime)}")

    if performance.active_time is not None:
        active = performance.active_time
        active_line = f"Tempo ativo: {active.user_value * 100:.1f}%"
        if active.stats.p50 is not None:
            active_line += f" (coorte mediana: {active.stats.p50 * 100:.1f}%)"
        lines.append(f"{active_line} {_status(active)}")

    for waste in performance.resource_waste:
        finding = waste.finding
        waste_line = f"**{waste.resource_type}**: {finding.user_value:.0f}"
        if finding.stats.p50 is not None:
            waste_line += f" (coorte mediana: {finding.stats.p50:.0f})"
        lines.append(f"{waste_line} {_status(finding)}")
    return lines
