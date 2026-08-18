"""T3.4 — self-contained inline SVG charts for the HTML report (no CDN,
no JS: every visual is plain, hand-emitted, well-formed SVG/XML computed
server-side). Two charts, per docs/implementacao.md T3.4:
- an ability timeline (player casts over the cohort's IQR band)
- a DPS-gap waterfall (analysis/dps_gap.py's per-ability contributions)

Accessibility (T3.4's own requirement, "não codificar informação apenas
por cor"): every colored marker carries a `<title>` child with the same
Portuguese label a legend line spells out once per chart — color is
never the only channel, marker SHAPE also differs by grade.
"""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

from botgitgud.analysis.alignment import AlignmentKind
from botgitgud.analysis.comparison import SpellComparison
from botgitgud.analysis.dps_gap import DpsGapReport

_GRADE_COLOR: dict[str, str] = {
    "green": "#2e7d32",
    "yellow": "#f9a825",
    "red": "#c62828",
    "insufficient": "#9e9e9e",
}
_GRADE_LABEL: dict[str, str] = {
    "green": "dentro do esperado",
    "yellow": "desvio moderado",
    "red": "desvio significativo",
    "insufficient": "amostra insuficiente",
}

_CHART_WIDTH = 640
_CHART_HEIGHT = 90
_MARGIN_X = 50


def _x(t: float, duration_s: float) -> float:
    if duration_s <= 0:
        return float(_MARGIN_X)
    span = _CHART_WIDTH - 2 * _MARGIN_X
    return _MARGIN_X + max(0.0, min(1.0, t / duration_s)) * span


def render_ability_timeline_svg(comparison: SpellComparison, duration_s: float) -> str:
    """One row per usage: a shape at the player's cast time (or the
    expected time for a miss), plus an IQR band (p25-p75) for MATCH steps
    — the cohort's own spread at that position, not just its median.
    """
    y = _CHART_HEIGHT / 2
    parts: list[str] = [
        f'<svg viewBox="0 0 {_CHART_WIDTH} {_CHART_HEIGHT}" '
        f'width="100%" height="{_CHART_HEIGHT}" role="img" '
        f'aria-label="{escape(f"Linha do tempo de {comparison.spell.name}")}">',
        f'<line x1="{_MARGIN_X}" y1="{y}" x2="{_CHART_WIDTH - _MARGIN_X}" y2="{y}" '
        f'stroke="#bbbbbb" stroke-width="1"/>',
    ]

    for idx, step in enumerate(comparison.alignment.steps):
        if step.kind is AlignmentKind.MATCH:
            grade = comparison.step_grades[idx]
            color = _GRADE_COLOR.get(grade.grade if grade else "insufficient", "#9e9e9e")
            label = _GRADE_LABEL.get(grade.grade if grade else "insufficient", "")
            assert step.ref_time is not None
            assert step.user_time is not None
            if grade is not None and grade.stats.p25 is not None and grade.stats.p75 is not None:
                x_lo = _x(grade.stats.p25, duration_s)
                x_hi = _x(grade.stats.p75, duration_s)
                parts.append(
                    f'<rect x="{x_lo:.1f}" y="{y - 6:.1f}" width="{max(0.0, x_hi - x_lo):.1f}" '
                    f'height="12" fill="{color}" fill-opacity="0.25"/>'
                )
            cx = _x(step.user_time, duration_s)
            parts.append(
                f'<circle cx="{cx:.1f}" cy="{y:.1f}" r="5" fill="{color}">'
                f"<title>{escape(label)} ({step.user_time:.1f}s)</title></circle>"
            )
        elif step.kind is AlignmentKind.MISSED:
            assert step.ref_time is not None
            cx = _x(step.ref_time, duration_s)
            size = 5
            parts.append(
                f'<g stroke="{_GRADE_COLOR["red"]}" stroke-width="2">'
                f'<line x1="{cx - size:.1f}" y1="{y - size:.1f}" '
                f'x2="{cx + size:.1f}" y2="{y + size:.1f}"/>'
                f'<line x1="{cx - size:.1f}" y1="{y + size:.1f}" '
                f'x2="{cx + size:.1f}" y2="{y - size:.1f}"/>'
                f"<title>{escape(f'não usado (esperado {step.ref_time:.1f}s)')}</title></g>"
            )
        else:  # EXTRA
            assert step.user_time is not None
            cx = _x(step.user_time, duration_s)
            size = 6
            points = (
                f"{cx:.1f},{y - size:.1f} {cx + size:.1f},{y:.1f} "
                f"{cx:.1f},{y + size:.1f} {cx - size:.1f},{y:.1f}"
            )
            parts.append(
                f'<polygon points="{points}" fill="#1565c0">'
                f"<title>{escape(f'uso extra ({step.user_time:.1f}s)')}</title></polygon>"
            )

    parts.append("</svg>")
    return "".join(parts)


def render_dps_gap_waterfall_svg(dps_gap: DpsGapReport) -> str:
    """Sequential waterfall: each gated ability's `delta_dps_pct`, plus
    "outras", as bars running left-to-right along a shared pp axis.
    """
    bars: list[tuple[str, float]] = [(a.spell.name, a.delta_dps_pct) for a in dps_gap.abilities]
    if dps_gap.n_other:
        bars.append((f"outras {dps_gap.n_other}", dps_gap.other_pct))

    height = 40 + 26 * max(1, len(bars))
    max_abs = max((abs(v) for _n, v in bars), default=1.0) or 1.0
    width = _CHART_WIDTH
    mid_x = width / 2
    scale = (width / 2 - 60) / max_abs

    parts: list[str] = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'role="img" aria-label="Decomposição do gap de DPS por habilidade">',
        f'<line x1="{mid_x:.1f}" y1="10" x2="{mid_x:.1f}" y2="{height - 10}" stroke="#999999"/>',
    ]
    if not bars:
        parts.append("</svg>")
        return "".join(parts)

    for i, (name, value) in enumerate(bars):
        y = 20 + i * 26
        color = "#2e7d32" if value >= 0 else "#c62828"
        label = "ganho acima da coorte" if value >= 0 else "abaixo da coorte"
        bar_w = abs(value) * scale
        x_start = mid_x if value >= 0 else mid_x - bar_w
        parts.append(
            f'<rect x="{x_start:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="18" fill="{color}">'
            f"<title>{escape(f'{name}: {value:+.1f}pp ({label})')}</title></rect>"
        )
        text_x = x_start - 4 if value >= 0 else x_start + bar_w + 4
        anchor = "end" if value >= 0 else "start"
        parts.append(
            f'<text x="{text_x:.1f}" y="{y + 13:.1f}" font-size="11" text-anchor="{anchor}" '
            f'fill="#222222">{escape(name)} ({value:+.1f}pp)</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


def render_grade_legend_svg() -> str:
    """A static, once-per-report legend mapping each color to its
    Portuguese label — the accompanying text the accessibility
    requirement asks for, spelled out explicitly rather than only via
    per-marker `<title>` tooltips.
    """
    order: Sequence[str] = ("green", "yellow", "red", "insufficient")
    parts = [
        f'<svg viewBox="0 0 {_CHART_WIDTH} 24" width="100%" height="24" '
        'role="img" aria-label="Legenda de cores">'
    ]
    x = 10
    for grade in order:
        color = _GRADE_COLOR[grade]
        label = _GRADE_LABEL[grade]
        parts.append(f'<circle cx="{x + 5}" cy="12" r="5" fill="{color}"/>')
        parts.append(
            f'<text x="{x + 16}" y="16" font-size="11" fill="#222222">{escape(label)}</text>'
        )
        x += 16 + len(label) * 6 + 20
    parts.append("</svg>")
    return "".join(parts)
