"""T2.2 — talent-build clustering by Jaccard similarity
(docs/implementacao.md T2.2, recomendação 6.3b).

`cluster_builds` does single-linkage clustering over the Jaccard-≥0.85
threshold graph (union-find) — "clustering aglomerativo simples" per the
task, no ML library needed. `analyze_build_divergence` is the report-level
consumer: clusters the analyzed player together with their own
covariate-matched cohort and, if the player's cluster is a minority
(< MINORITY_THRESHOLD of the pool), returns the "BUILD DIVERGENTE" finding
that must open the report before any timing analysis.

D-26 (docs/desvios.md): WCL's `talentTree[].id` does not resolve through
`gameData.ability(id)` (verified live — returns null for real talent
entries, unlike genuine spell IDs), and no Blizzard Game Data endpoint for
the new-style talent-tree node IDs was found either (`/data/wow/talent/{id}`
and `/data/wow/spell-tree-node/{id}` both 404 live). There is no talent
name catalog anywhere in this project. Distinguishing talents are reported
by `(nodeID, rank)` instead of by name.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from botgitgud.domain.models import PlayerLog

JACCARD_THRESHOLD = 0.85
MINORITY_THRESHOLD = 0.20


def jaccard_similarity(a: frozenset[tuple[int, int]], b: frozenset[tuple[int, int]]) -> float:
    """1.0 for two empty sets (vacuously identical — never actually reached
    by match_cohort/cluster_builds, both of which exclude empty-pairs logs
    upstream, but keeps this a total function), 0.0 if only one is empty.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass(frozen=True, slots=True)
class TalentCluster:
    cluster_id: int  # 0 = largest cluster (dominant), ascending by size thereafter
    representative: frozenset[tuple[int, int]]
    members: tuple[PlayerLog, ...]


def cluster_builds(logs: Sequence[PlayerLog]) -> list[TalentCluster]:
    """Groups `logs` into clusters via single-linkage over
    jaccard_similarity >= JACCARD_THRESHOLD (union-find, O(n^2) pairwise —
    fine at cohort scale, no ML dependency needed). Logs with an empty
    `build.talent_pairs` (missing combatantInfo) are silently excluded —
    there's no covariate to cluster them by. Returned list is sorted
    largest-first, so `clusters[0]` is always the dominant build.
    """
    usable = [log for log in logs if log.build.talent_pairs]
    n = len(usable)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(n):
        for j in range(i + 1, n):
            pairs_i = usable[i].build.talent_pairs
            pairs_j = usable[j].build.talent_pairs
            if jaccard_similarity(pairs_i, pairs_j) >= JACCARD_THRESHOLD:
                union(i, j)

    groups: dict[int, list[PlayerLog]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(usable[i])

    ordered = sorted(groups.values(), key=len, reverse=True)
    return [
        TalentCluster(
            cluster_id=cid,
            representative=members[0].build.talent_pairs,
            members=tuple(members),
        )
        for cid, members in enumerate(ordered)
    ]


@dataclass(frozen=True, slots=True)
class TalentDifference:
    node_id: int
    dominant_rank: int | None  # None = this node isn't picked in the dominant build
    player_rank: int | None  # None = this node isn't picked in the player's build


def diff_talent_pairs(
    dominant: frozenset[tuple[int, int]], player: frozenset[tuple[int, int]]
) -> list[TalentDifference]:
    dominant_by_node = dict(dominant)
    player_by_node = dict(player)
    node_ids = sorted(set(dominant_by_node) | set(player_by_node))
    return [
        TalentDifference(
            node_id=node_id,
            dominant_rank=dominant_by_node.get(node_id),
            player_rank=player_by_node.get(node_id),
        )
        for node_id in node_ids
        if dominant_by_node.get(node_id) != player_by_node.get(node_id)
    ]


@dataclass(frozen=True, slots=True)
class BuildDivergence:
    player_cluster_n: int
    total_n: int
    dominant_cluster_n: int
    dominant_median_dps: float | None
    player_median_dps: float | None
    differences: tuple[TalentDifference, ...]

    @property
    def player_pct(self) -> float:
        return self.player_cluster_n / self.total_n if self.total_n else 0.0

    @property
    def dominant_pct(self) -> float:
        return self.dominant_cluster_n / self.total_n if self.total_n else 0.0

    @property
    def estimated_gain_pct(self) -> float | None:
        """T3.3: the same `(dominant - player) / player * 100` delta
        report/build_divergence_text.py already displays — the dominant
        build's median DPS taken as the achievable target.
        """
        if not self.dominant_median_dps or not self.player_median_dps:
            return None
        return (self.dominant_median_dps - self.player_median_dps) / self.player_median_dps * 100


def analyze_build_divergence(
    target: PlayerLog, cohort_logs: Sequence[PlayerLog]
) -> BuildDivergence | None:
    """Clusters `target` together with `cohort_logs` (the player's own
    covariate-matched cohort, T2.1) and returns a BuildDivergence when the
    player's cluster is a genuine minority (< MINORITY_THRESHOLD of the
    pool) — None when the player has no talent data, when there's only one
    cluster (identical builds), or when the player is already in (or tied
    with) the dominant cluster.
    """
    if not target.build.talent_pairs:
        return None

    pool = [target, *cohort_logs]
    clusters = cluster_builds(pool)
    if len(clusters) <= 1:
        return None

    target_cluster = next(c for c in clusters if any(m is target for m in c.members))
    dominant_cluster = clusters[0]
    if target_cluster is dominant_cluster:
        return None

    total_n = sum(len(c.members) for c in clusters)
    player_pct = len(target_cluster.members) / total_n if total_n else 0.0
    if player_pct >= MINORITY_THRESHOLD:
        return None

    dominant_dps = [m.dps for m in dominant_cluster.members if m.dps is not None]
    player_dps = [m.dps for m in target_cluster.members if m.dps is not None]
    differences = diff_talent_pairs(dominant_cluster.representative, target_cluster.representative)
    return BuildDivergence(
        player_cluster_n=len(target_cluster.members),
        total_n=total_n,
        dominant_cluster_n=len(dominant_cluster.members),
        dominant_median_dps=statistics.median(dominant_dps) if dominant_dps else None,
        player_median_dps=statistics.median(player_dps) if player_dps else None,
        differences=tuple(differences),
    )
