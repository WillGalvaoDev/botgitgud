"""T2.2 split of the "BUILD DIVERGENTE" block out of report/text.py, to
keep both files under the 300-line limit (docs/implementacao.md T2.2).

D-26 (docs/desvios.md): WCL's talentTree node IDs don't resolve to names
anywhere in this project — verified live against `gameData.ability(id)`
(returns null for real talentTree `id`s, unlike genuine spell IDs) and
against Blizzard's `/data/wow/talent/{id}` and `/data/wow/spell-tree-node/
{id}` (both 404 live). Differences are shown by nodeID/rank instead.
"""

from __future__ import annotations

from botgitgud.analysis.talent_cluster import BuildDivergence

_SEPARATOR = "=" * 42
_MAX_DIFFERENCES_SHOWN = 5


def _fmt_dps(value: float | None) -> str:
    return f"{value:,.0f}" if value is not None else "n/d"


def _render_talent_difference(
    node_id: int, dominant_rank: int | None, player_rank: int | None
) -> str:
    dominant = f"rank {dominant_rank}" if dominant_rank is not None else "não escolhido"
    player = f"rank {player_rank}" if player_rank is not None else "não escolhido"
    return f"nó {node_id} (dominante: {dominant} / você: {player})"


def render_build_divergence(divergence: BuildDivergence) -> list[str]:
    """T2.2: must open the report BEFORE any timing analysis — "otimizar a
    rotação de uma build inferior é conselho de baixo valor."
    """
    diffs = divergence.differences[:_MAX_DIFFERENCES_SHOWN]
    diff_text = "; ".join(
        _render_talent_difference(d.node_id, d.dominant_rank, d.player_rank) for d in diffs
    )
    remaining = len(divergence.differences) - len(diffs)
    if remaining > 0:
        diff_text += f"; +{remaining} mais"

    lines = [
        "🧬 **BUILD DIVERGENTE**",
        (
            f"Sua build aparece em {divergence.player_pct * 100:.0f}% dos top parses "
            f"({divergence.player_cluster_n}/{divergence.total_n} logs)."
        ),
        (
            f"A build dominante ({divergence.dominant_pct * 100:.0f}%, "
            f"{divergence.dominant_cluster_n}/{divergence.total_n}) difere em: {diff_text}."
        ),
    ]
    if divergence.dominant_median_dps is not None and divergence.player_median_dps is not None:
        delta_pct = None
        if divergence.player_median_dps:
            delta_pct = (
                (divergence.dominant_median_dps - divergence.player_median_dps)
                / divergence.player_median_dps
                * 100
            )
        delta_fmt = f" (Δ {delta_pct:+.1f}%)" if delta_pct is not None else ""
        lines.append(
            f"DPS mediano da build dominante: {_fmt_dps(divergence.dominant_median_dps)} "
            f"vs {_fmt_dps(divergence.player_median_dps)} na sua build{delta_fmt}."
        )
    lines.append("⚠️ Antes de otimizar rotação, avalie a troca de build.")
    lines.append(_SEPARATOR)
    return lines
