"""T2.3 split of quantile-grading rendering out of report/text.py, to keep
both files under the 300-line limit (T2.3).

Replaces the old fixed 10s/25s `_match_status` thresholds with a grade
relative to each position's own empirical reference distribution
(analysis/grading.py), plus Benjamini-Hochberg (FDR=0.10) multiple-
comparison control over every yellow/red MATCH step in the report —
findings that don't survive move to a collapsed "Desvios menores" section
instead of appearing inline as if they were as trustworthy as the rest.
"""

from __future__ import annotations

from collections.abc import Sequence

from botgitgud.analysis.comparison import SpellComparison, StepGrade
from botgitgud.analysis.grading import FDR, benjamini_hochberg, two_tailed_p_value

_GRADE_EMOJI: dict[str, str] = {"green": "🟢", "yellow": "🟡", "red": "🔴"}

# (comparison_idx, step_idx) — step_idx is 0-based, matching
# SpellComparison.step_grades' own indexing (not the 1-based "Uso #N"
# shown to the user).
DeviationKey = tuple[int, int]


def compute_minor_deviation_keys(comparisons: Sequence[SpellComparison]) -> set[DeviationKey]:
    """T2.3 step 5: BH over every yellow/red MATCH step's two-tailed
    p-value (derived from its empirical quantile). Green and insufficient
    steps are never candidates — they aren't "deviations" to begin with.
    """
    candidates: list[DeviationKey] = []
    p_values: list[float] = []
    for c_idx, c in enumerate(comparisons):
        for s_idx, sg in enumerate(c.step_grades):
            if sg is None or sg.grade not in ("yellow", "red") or sg.quantile is None:
                continue
            candidates.append((c_idx, s_idx))
            p_values.append(two_tailed_p_value(sg.quantile))

    survives = benjamini_hochberg(p_values, fdr=FDR)
    return {key for key, ok in zip(candidates, survives, strict=True) if not ok}


def render_match_step_line(
    display_idx: int, user_time: float, ref_time: float, delta: float, grade: StepGrade
) -> str:
    sign = "+" if delta > 0 else ""
    if grade.grade == "insufficient":
        status = f"⚪ amostra insuficiente (n={grade.stats.n})"
    else:
        status = _GRADE_EMOJI[grade.grade]
    ci_fmt = ""
    if grade.ci90 is not None:
        lo, hi = grade.ci90
        ci_fmt = f" (IC90: {lo:.0f}-{hi:.0f}s)"
    return (
        f"Uso #{display_idx} | Player: {user_time:.1f}s | Ideal: {ref_time:.1f}s{ci_fmt} "
        f"| Delta: {sign}{delta:.1f}s {status}"
    )


def render_minor_deviations_section(
    comparisons: Sequence[SpellComparison], minor_keys: set[DeviationKey]
) -> list[str]:
    if not minor_keys:
        return []
    lines = ["", "🔽 **Desvios menores (não significativos)**", "-" * 42]
    for c_idx, c in enumerate(comparisons):
        for s_idx, step in enumerate(c.alignment.steps):
            if (c_idx, s_idx) not in minor_keys:
                continue
            grade = c.step_grades[s_idx]
            assert grade is not None
            assert step.user_time is not None
            assert step.ref_time is not None
            assert step.delta is not None
            line = render_match_step_line(
                s_idx + 1, step.user_time, step.ref_time, step.delta, grade
            )
            lines.append(f"**{c.spell.name}** {line}")
    return lines
