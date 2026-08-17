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

The MATCH delta color thresholds (10s/25s) are unchanged from legacy on
purpose — replacing them with per-ability quantile-based grading is T2.3's
job, out of scope here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from botgitgud.analysis.alignment import AlignmentKind
from botgitgud.analysis.comparison import SpellComparison

_GREEN_THRESHOLD_S = 10.0
_YELLOW_THRESHOLD_S = 25.0
_SEPARATOR = "=" * 42


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


def _fmt_duration(seconds: float) -> str:
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


def _fmt_dps(value: float | None) -> str:
    return f"{value:,.0f}" if value is not None else "n/d"


def _fmt_percentile(value: float | None) -> str:
    return f"{value:.0f}" if value is not None else "n/d"


def _match_status(delta: float) -> str:
    abs_delta = abs(delta)
    if abs_delta <= _GREEN_THRESHOLD_S:
        return "🟢"
    if abs_delta <= _YELLOW_THRESHOLD_S:
        return "🟡"
    return "🔴"


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
        _SEPARATOR,
    ]
    return lines


def _render_missed_usage_section(comparisons: Sequence[SpellComparison]) -> list[str]:
    with_missed = [c for c in comparisons if c.alignment.n_missed > 0]
    if not with_missed:
        return []

    lines = ["", "⛔ **USOS PERDIDOS**", "-" * 42]
    for c in with_missed:
        missed_times = [s.ref_time for s in c.alignment.steps if s.kind is AlignmentKind.MISSED]
        times_fmt = ", ".join(f"{t:.1f}s" for t in missed_times)
        lines.append(
            f"**{c.spell.name}**: {c.alignment.n_missed} uso(s) perdido(s) "
            f"— esperado(s) aos {times_fmt}"
        )
    return lines


def _render_spell_block(c: SpellComparison) -> list[str]:
    n_user = c.alignment.n_matched + c.alignment.n_extra
    n_ref_median = c.cadence.n_usages_median
    lines = [
        "",
        f"**{c.spell.name}** (Tipo: {c.cd_type} | Pres: {c.presence * 100:.0f}%)",
        f"Usos: {n_user} (coorte: {n_ref_median:.1f})",
        "-" * 30,
    ]
    for idx, step in enumerate(c.alignment.steps, start=1):
        if step.kind is AlignmentKind.MATCH:
            assert step.user_time is not None
            assert step.ref_time is not None
            assert step.delta is not None
            sign = "+" if step.delta > 0 else ""
            status = _match_status(step.delta)
            lines.append(
                f"Uso #{idx} | Player: {step.user_time:.1f}s | Ideal: {step.ref_time:.1f}s "
                f"| Delta: {sign}{step.delta:.1f}s {status}"
            )
        elif step.kind is AlignmentKind.MISSED:
            assert step.ref_time is not None
            lines.append(f"Uso #{idx} | Esperado ~{step.ref_time:.1f}s | NÃO USADO ⛔")
        else:  # EXTRA
            assert step.user_time is not None
            lines.append(f"Uso #{idx} | Player: {step.user_time:.1f}s | Uso extra")
    return lines


def render_report(header: ReportHeader, comparisons: Sequence[SpellComparison]) -> str:
    lines = _render_header(header)

    if not comparisons:
        lines.append("")
        lines.append("⚡ Nenhum Major/Minor CD elegível encontrado.")
        lines.append(_SEPARATOR)
        return "\n".join(lines)

    lines.extend(_render_missed_usage_section(comparisons))

    major = [c for c in comparisons if c.cd_type == "MAJOR"]
    minor = [c for c in comparisons if c.cd_type == "MINOR"]

    if major:
        lines.append("")
        lines.append("🔥 **OFFENSIVE MAJOR CDS**")
        lines.append("-" * 42)
        for c in major:
            lines.extend(_render_spell_block(c))

    if minor:
        lines.append("")
        lines.append("⚡ **MINOR CDS / BURST UTILITIES**")
        lines.append("-" * 42)
        for c in minor:
            lines.extend(_render_spell_block(c))

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
