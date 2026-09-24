"""T3.3 — renders the "🎯 TOP 3 AÇÕES" section (docs/architecture.md
T3.3's mandated report structure, item 2 — right after the header, before
"de onde veio o gap de DPS").
"""

from __future__ import annotations

from botgitgud.analysis.findings import TopPriorities

UNQUANTIFIED_IMPACT_LABEL = "impacto não quantificado"

_SEPARATOR_THIN = "-" * 42


def render_top_actions_section(top_actions: TopPriorities) -> list[str]:
    lines = [
        "",
        "🎯 **TOP 3 PRIORIDADES**",
        _SEPARATOR_THIN,
        "Habilidades ordenadas por déficit observado; "
        "outras prioridades têm impacto não quantificado.",
    ]
    if not top_actions.level1 and not top_actions.level2:
        lines.append("Não há dados suficientes para definir prioridades confiáveis nesta luta.")
        return lines

    for i, finding in enumerate(top_actions.level1, start=1):
        observed = finding.observed_deficit_player_pp
        if observed is not None:
            impact = f"déficit observado: {observed:+.1f}pp"
        else:
            impact = "déficit percentual indisponível"
        lines.append(f"{i}. **{finding.title}** — {impact} (confiança: {finding.confidence})")
        lines.append(f"   {finding.detail}")
    offset = len(top_actions.level1)
    for i, finding in enumerate(top_actions.level2, start=offset + 1):
        lines.append(
            f"{i}. **{finding.title}** — {UNQUANTIFIED_IMPACT_LABEL} "
            f"(confiança: {finding.confidence})"
        )
        lines.append(f"   {finding.detail}")
    return lines
