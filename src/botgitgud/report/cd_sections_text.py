"""T3.2 split of report/text.py's CD-comparison rendering (missed usage +
MAJOR/MINOR blocks + collapsed minor-deviations section) into its own
module, to make room for the new "de onde veio o gap de DPS" section
without pushing text.py past the 300-line limit (docs/implementacao.md
T1.6). Pure extraction — no behavior change from the T2.3-era code.
"""

from __future__ import annotations

from collections.abc import Sequence

from botgitgud.analysis.alignment import AlignmentKind
from botgitgud.analysis.comparison import SpellComparison
from botgitgud.report.grading_text import (
    compute_minor_deviation_keys,
    render_match_step_line,
    render_minor_deviations_section,
)

_SEPARATOR_THIN = "-" * 42


def _render_missed_usage_section(comparisons: Sequence[SpellComparison]) -> list[str]:
    with_missed = [c for c in comparisons if c.alignment.n_missed > 0]
    if not with_missed:
        return []

    lines = ["", "⛔ **USOS PERDIDOS**", _SEPARATOR_THIN]
    for c in with_missed:
        missed_times = [s.ref_time for s in c.alignment.steps if s.kind is AlignmentKind.MISSED]
        times_fmt = ", ".join(f"{t:.1f}s" for t in missed_times)
        lines.append(
            f"**{c.spell.name}**: {c.alignment.n_missed} uso(s) perdido(s) "
            f"— esperado(s) aos {times_fmt}"
        )
    return lines


def _render_spell_block(
    c: SpellComparison, minor_keys: set[tuple[int, int]], c_idx: int
) -> list[str]:
    n_user = c.alignment.n_matched + c.alignment.n_extra
    n_ref_median = c.cadence.n_usages_median
    lines = [
        "",
        f"**{c.spell.name}** (Tipo: {c.cd_type} | Pres: {c.presence * 100:.0f}%)",
        f"Usos: {n_user} (coorte: {n_ref_median:.1f})",
        "-" * 30,
    ]
    for s_idx, step in enumerate(c.alignment.steps):
        idx = s_idx + 1
        if step.kind is AlignmentKind.MATCH:
            if (c_idx, s_idx) in minor_keys:
                continue  # T2.3: rendered in the collapsed section instead
            assert step.user_time is not None
            assert step.ref_time is not None
            assert step.delta is not None
            grade = c.step_grades[s_idx]
            assert grade is not None
            lines.append(
                render_match_step_line(idx, step.user_time, step.ref_time, step.delta, grade)
            )
        elif step.kind is AlignmentKind.MISSED:
            assert step.ref_time is not None
            lines.append(f"Uso #{idx} | Esperado ~{step.ref_time:.1f}s | NÃO USADO ⛔")
        else:  # EXTRA
            assert step.user_time is not None
            lines.append(f"Uso #{idx} | Player: {step.user_time:.1f}s | Uso extra")
    return lines


def render_cd_sections(comparisons: Sequence[SpellComparison]) -> list[str]:
    """T3.1 items 6/7: usos perdidos, então os blocos MAJOR/MINOR, então
    os desvios menores colapsados — nesta ordem, sempre por último no
    relatório completo (report/text.py's render_report).
    """
    lines = list(_render_missed_usage_section(comparisons))

    # T2.3: BH runs over the WHOLE report's yellow/red steps before any
    # spell block renders, so major/minor ordering doesn't bias which
    # findings survive.
    minor_keys = compute_minor_deviation_keys(comparisons)
    indexed = list(enumerate(comparisons))
    major = [(i, c) for i, c in indexed if c.cd_type == "MAJOR"]
    minor = [(i, c) for i, c in indexed if c.cd_type == "MINOR"]

    if major:
        lines.append("")
        lines.append("🔥 **OFFENSIVE MAJOR CDS**")
        lines.append(_SEPARATOR_THIN)
        for i, c in major:
            lines.extend(_render_spell_block(c, minor_keys, i))

    if minor:
        lines.append("")
        lines.append("⚡ **MINOR CDS / BURST UTILITIES**")
        lines.append(_SEPARATOR_THIN)
        for i, c in minor:
            lines.extend(_render_spell_block(c, minor_keys, i))

    lines.extend(render_minor_deviations_section(comparisons, minor_keys))
    return lines
