"""T3.3 — renders the "🎯 TOP 3 AÇÕES" section (docs/implementacao.md
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
        "Primeiro vêm melhorias com impacto medido; depois, prioridades "
        "relevantes sem ganho de DPS quantificado.",
    ]
    if not top_actions.level1 and not top_actions.level2:
        lines.append("Não há dados suficientes para definir prioridades confiáveis nesta luta.")
        return lines

    for i, finding in enumerate(top_actions.level1, start=1):
        lines.append(
            f"{i}. **{finding.title}** — ganho estimado: {finding.estimated_gain_pct:+.1f}pp "
            f"(confiança: {finding.confidence})"
        )
        lines.append(f"   {finding.detail}")
    offset = len(top_actions.level1)
    for i, finding in enumerate(top_actions.level2, start=offset + 1):
        lines.append(
            f"{i}. **{finding.title}** — {UNQUANTIFIED_IMPACT_LABEL} "
            f"(confiança: {finding.confidence})"
        )
        lines.append(f"   {finding.detail}")
    return lines
