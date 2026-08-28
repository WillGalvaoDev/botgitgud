"""T2.2 — Jaccard similarity between two talent builds
(docs/implementacao.md T2.2, recomendação 6.3b).

EC.4: the report-level consumer that used to live here (`cluster_builds`/
`analyze_build_divergence`/`BuildDivergence`) was removed. It clustered the
analyzed player against their OWN execution-matched cohort
(`analysis/cohort_match.py`'s `matched_logs` — a population already
filtered/biased by `match_cohort`'s covariates, `talent_cluster` included
under policy v1) and attached a causal `estimated_gain_pct` derived from
that cluster's median DPS delta. That is exactly the pattern the Setup
Analysis milestone (SA.1-SA.6) exists to replace: `analysis/
setup_talents.py`'s `compare_talent_build` already answers "how does this
player's build compare to what's observed among strong performers" against
the Encounter Benchmark — an unfiltered, purely observational population,
with `estimated_gain_pct` deliberately absent from its contract. Keeping
both alive risked exactly the two-contradictory-findings problem the EC.4
ticket named directly. `jaccard_similarity`/`JACCARD_THRESHOLD` remain
here — `cohort_match.py`'s v1 matching policy still uses them to decide
whether a candidate's build is "close enough to the target" (unrelated to
the removed report-level clustering/divergence finding).

D-26 (docs/desvios.md): WCL's `talentTree[].id` does not resolve through
`gameData.ability(id)` (verified live — returns null for real talent
entries, unlike genuine spell IDs), and no Blizzard Game Data endpoint for
the new-style talent-tree node IDs was found either (`/data/wow/talent/{id}`
and `/data/wow/spell-tree-node/{id}` both 404 live). There is no talent
name catalog anywhere in this project. Distinguishing talents are reported
by `(nodeID, rank)` instead of by name.
"""

from __future__ import annotations

JACCARD_THRESHOLD = 0.85


def jaccard_similarity(a: frozenset[tuple[int, int]], b: frozenset[tuple[int, int]]) -> float:
    """1.0 for two empty sets (vacuously identical — never actually reached
    by match_cohort, which excludes empty-pairs logs upstream, but keeps
    this a total function), 0.0 if only one is empty.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
