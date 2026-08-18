"""T3.3 — renders the "🎯 TOP 3 AÇÕES" section (docs/implementacao.md
T3.3's mandated report structure, item 2 — right after the header, before
"de onde veio o gap de DPS").
"""

from __future__ import annotations

from collections.abc import Sequence

from botgitgud.analysis.findings import Finding

_SEPARATOR_THIN = "-" * 42


def render_top_actions_section(top_actions: Sequence[Finding]) -> list[str]:
    lines = ["", "🎯 **TOP 3 AÇÕES**", _SEPARATOR_THIN]
    if not top_actions:
        lines.append("✅ Nenhum problema material detectado.")
        return lines

    for i, finding in enumerate(top_actions, start=1):
        assert finding.estimated_gain_pct is not None  # select_top_actions already filtered
        lines.append(
            f"{i}. **{finding.title}** — ganho estimado: {finding.estimated_gain_pct:+.1f}pp "
            f"(confiança: {finding.confidence})"
        )
        lines.append(f"   {finding.detail}")
    return lines
