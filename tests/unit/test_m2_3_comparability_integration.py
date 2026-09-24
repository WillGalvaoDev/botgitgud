"""M2.3 — integration tests for comparability (docs/methodology.md §5):
connects M2.1 (reference_eligibility.py) and
M2.2 (metric_population.py) — both unmodified, byte-identical — to
run_analysis, the report contract and persisted comparability-provenance-v1.

Matrix defined before implementation: cohort_match refactor equivalence
against the actual pre-M2.3 implementation (§4.1), population routing and
non-borrowing across metric_id vs. the ledger through real run_analysis
replays (§5, AC1), ledger insufficiency with a sufficient metric (§6.2),
the aspirational-ledger guard exercised through run_analysis (§6.3),
provenance round-trip through encode/decode and Store persistence (§7,
AC2), determinism/permutation of full run_analysis provenance including
the duplicate-conflict quarantine, contract/render
safety (§6.4), a read-only replay of real metadata, and structural checks
anchoring AC5 (M2.1/M2.2 untouched) and AC6.

Contracts: docs/methodology.md §3-§5.
"""

from __future__ import annotations

import dataclasses
import hashlib
import itertools
import json
import math
import re
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import duckdb
import pytest
from dir_snapshot import diff_snapshots, snapshot_directory
from test_cohort_match import _log as _cm_log
from test_cohort_match import _target as _cm_target
from test_log_fetcher import (
    _events_response,
    _meta_response,
    _percentile_response,
    _report_rankings_response,
)
from test_m2_2_metric_population import _make_log
from test_pipeline import (
    N_REFS,
    PRIMARY_FIGHT,
    PRIMARY_REPORT,
    _build_deps,
    _DispatchTransport,
    _happy_path_responses,
    _req,
    _zone_partitions_response,
)

from botgitgud.analysis.benchmark_aggregate import player_identity
from botgitgud.analysis.cohort import COHORT_MIN_HARD, COHORT_TARGET_N
from botgitgud.analysis.cohort_match import (
    DuplicateConflictReport,
    HygieneReport,
    MatchReport,
    hygienic_candidates,
    match_cohort,
    match_covariates,
    quarantine_conflicting_duplicates,
)
from botgitgud.analysis.comparability_provenance import (
    COMPARABILITY_PROVENANCE_VERSION,
    ComparabilityProvenance,
    build_comparability_provenance,
    decode_comparability_provenance,
    encode_comparability_provenance,
    summarize_eligibility,
    summarize_hygiene,
    summarize_ledger,
    summarize_metrics,
)
from botgitgud.analysis.measurement import compare_damage, damage_reference_id
from botgitgud.analysis.metric_population import (
    METRIC_POPULATION_POLICY_VERSION,
    MetricPopulationSet,
    select_metric_populations,
)
from botgitgud.analysis.pipeline import run_analysis
from botgitgud.analysis.reference_eligibility import (
    REFERENCE_ELIGIBILITY_POLICY_VERSION,
    ReferenceEligibilityPopulation,
    evaluate_references,
)
from botgitgud.domain.models import (
    DEFAULT_MATCHING_POLICY_VERSION,
    FightRef,
    PlayerBuild,
    PlayerLog,
    RunManifest,
)
from botgitgud.ingest.store import Store
from botgitgud.report.coaching_answer import render_coaching_answer
from botgitgud.report.contract import build_report_contract
from botgitgud.report.text import render_report

_M23_BASE_SHA = "edba6e62d144811724efe3a2f9bdf0d6023c64ee"  # M2.2 closure, pre-M2.3 cohort_match.py


def _run_analysis_with_fake_references(
    tmp_path: Path, target: PlayerLog, references: list[PlayerLog]
) -> tuple[Any, Any]:
    """Real `run_analysis`, with only acquisition substituted: the target's
    own log and the candidate pool are faked, everything downstream
    (quarantine, hygiene, M2.1, the ledger, M2.2 populations, dps_gap,
    performance, the contract and rendering) runs unmodified production
    code. Returns
    `(result, deps)` — `deps.store` stays open so a caller that needs the
    persisted `runs` row can query it with `with deps.store as store:`.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    transport = _DispatchTransport({})
    deps = _build_deps(tmp_path, transport)
    deps.catalog.learn(1, "Example", "wcl")
    with (
        patch.object(deps.fetcher, "fetch", return_value=target),
        patch.object(deps.store, "read_candidate_pool", return_value=[]),
        patch("botgitgud.analysis.pipeline.fetch_cohort_logs", return_value=references),
        patch("botgitgud.analysis.pipeline.get_current_partition", return_value=4),
    ):
        result = run_analysis(_req(target.build.character_name), deps)
    return result, deps


def _load_pre_m23_match_cohort() -> Any:
    """Loads the actual pre-M2.3 `match_cohort` — the single function that
    did hygiene and covariate degradation together, at the M2.2 closure
    commit this unit was built on — from git history and execs
    it as a real module. This is the independent oracle the equivalence contract
    requires: not the new code compared to its own composition, but the
    real prior implementation. Its own imports (unchanged dependencies)
    resolve normally since it executes as ordinary Python; it needs a real
    `sys.modules` entry (not just a dict namespace) because `from __future__
    import annotations` makes its dataclass field types lazy strings that
    `dataclasses` resolves via `sys.modules[cls.__module__]`.
    """
    import sys
    import types

    repo_root = Path(__file__).resolve().parents[2]
    source = subprocess.run(
        ["git", "show", f"{_M23_BASE_SHA}:src/botgitgud/analysis/cohort_match.py"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    module_name = "botgitgud_pre_m23_cohort_match"
    module = types.ModuleType(module_name)
    module.__dict__["__name__"] = module_name
    sys.modules[module_name] = module
    try:
        exec(compile(source, "<pre-M2.3 cohort_match.py>", "exec"), module.__dict__)
    finally:
        del sys.modules[module_name]
    return module.match_cohort


_M22_CLOSURE_SHA = "b6241df9b53f98226f975f8b227474b700ce0e08"  # HEAD: M2.2 closed, pre-M2.3


def _load_m22_closure_module(name: str, path: str) -> Any:
    """Execs `path` exactly as committed at the M2.2 closure (git history) as
    a real module — the pre-M2.3 consumers (`match_cohort`, `compare_metrics`,
    `compare_damage`) that the per-consumer "N antes/depois" table is measured on.
    """
    import sys
    import types

    source = subprocess.run(
        ["git", "show", f"{_M22_CLOSURE_SHA}:{path}"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    module = types.ModuleType(name)
    module.__dict__["__name__"] = name
    sys.modules[name] = module
    try:
        exec(compile(source, f"<M2.2 closure {path}>", "exec"), module.__dict__)
    finally:
        del sys.modules[name]
    return module


# Numbers a renderer must never publish for a comparison that was not computed
# (docs/methodology.md): a cohort/reference statistic, gap or delta, or any DPS/percent
# quantity. Legitimate numbers (the player's own observation, the N actually
# used by a computed comparison) are checked positively by each test instead.
_UNCOMPUTED_COMPARISON_CLAIM = re.compile(
    r"(?i)(?:mediana|m[eé]dia|gap|delta|coorte|refer[eê]ncia|compar[aá]vei)s?[^.\n]*\d"
    r"|\d[\d.,]*\s*(?:dps|%)"
)


def _assert_no_uncomputed_comparison_numbers(text: str) -> None:
    match = _UNCOMPUTED_COMPARISON_CLAIM.search(text)
    assert match is None, f"published a comparison number for a non-computed comparison: {match}"


def _render_all(result: Any, contract: Any) -> tuple[str, str]:
    """CLI and Discord text with every real field passed through the CLI
    renderer (not left at its defaults), so the section-header absences below
    prove the fields' absence on the rendered path, not a skipped argument.
    """
    cli_text = render_report(
        result.header,
        result.comparisons,
        None,
        result.performance,
        result.dps_gap,
        result.top_actions,
        setup=result.setup_analysis,
        confidence=contract.confianca,
        core_abilities=result.core_abilities,
        proc_analysis=result.proc_analysis,
        external_dps_context=result.external_dps_context,
    )
    return cli_text, render_coaching_answer(contract)


_NO_PRIORITY_DISCORD_TEXT = (
    "A análise não encontrou uma prioridade de coaching sustentada pelos dados."
)


def test_uncomputed_comparison_number_check_rejects_fabricated_comparison_text() -> None:
    """Pins the checker that guards every insufficient-ledger scenario: a
    renderer that published a median/gap for a comparison that was never
    computed must be rejected, while the real answer passes.
    """
    fabricated = "Comparacao da coorte: mediana 123456.7 DPS; gap 99999.9 DPS."
    with pytest.raises(AssertionError, match="non-computed comparison"):
        _assert_no_uncomputed_comparison_numbers(fabricated)
    _assert_no_uncomputed_comparison_numbers(_NO_PRIORITY_DISCORD_TEXT)


# --- §4.1/§9.1: cohort_match refactor equivalence against the real pre-M2.3 code -


def _cohort_log(name: str, **kwargs: Any) -> PlayerLog:
    return _make_log(character_name=name, **kwargs)


def _report_as_tuple(report: Any) -> tuple[object, ...]:
    """Compares MatchReport instances across the old (git-history) and new
    module namespaces by value — dataclass `__eq__` requires the same
    class object, which two independently exec'd/imported `MatchReport`
    classes never share even when every field agrees.
    """
    return dataclasses.astuple(report)


def test_match_cohort_equals_the_actual_pre_m23_implementation() -> None:
    """The independent oracle is the REAL prior
    implementation (git history), not the new code compared to its own
    `hygienic_candidates` + `match_covariates` composition — that
    comparison is close to tautological, since `match_cohort` is DEFINED
    as that composition (§4.1). Sweeps the fixtures `test_cohort_match.py`
    already uses for hygiene/degradation plus a few synthetic ones with
    every hygiene rule active (self, non-kill, pull-dedup, player-dedup),
    in both matching policy versions.
    """
    old_match_cohort = _load_pre_m23_match_cohort()
    target = _cm_target()

    scenarios: list[tuple[PlayerLog, list[PlayerLog], dict[str, Any]]] = [
        # test_cohort_match.py's own hygiene fixture, verbatim.
        (
            target,
            [
                _cm_log(name="target"),
                _cm_log(kill=False),
                _cm_log(name="PullLow", report_code="SAMEPULL", percentile=20.0),
                _cm_log(name="PullHigh", report_code="SAMEPULL", percentile=90.0),
                _cm_log(name="Repeat", report_code="PLAYERPULL1", percentile=40.0),
                _cm_log(name="repeat", report_code="PLAYERPULL2", percentile=80.0),
            ],
            {"min_n": 1},
        ),
        # Covariate degradation ladder, v2, with duplicated pulls/players mixed in.
        (
            target,
            [
                *(
                    _cm_log(name=f"C{i}", report_code=f"R{i:03d}", tier_pieces=(0 if i < 3 else 4))
                    for i in range(12)
                ),
                _cm_log(name="Target", report_code="SELFPULL"),
                _cm_log(name="NonKill", report_code="DEAD", kill=False),
                _cm_log(name="DupA", report_code="SAMEPULL2", percentile=10.0),
                _cm_log(name="DupA", report_code="SAMEPULL2", percentile=99.0),
            ],
            {"min_n": COHORT_TARGET_N, "matching_policy_version": "v2"},
        ),
        # v1 (talent_cluster included), item_level/tier_pieces mismatches.
        (
            target,
            [
                _cm_log(
                    name=f"V1_{i}",
                    report_code=f"V1R{i:03d}",
                    item_level=(260.0 if i < 4 else 283.0),
                    tier_pieces=(1 if i < 2 else 4),
                )
                for i in range(10)
            ],
            {"min_n": COHORT_TARGET_N, "matching_policy_version": "v1"},
        ),
    ]

    for scenario_target, candidates, kwargs in scenarios:
        for ordering in (candidates, list(reversed(candidates))):
            old_filtered, old_report = old_match_cohort(scenario_target, ordering, **kwargs)
            new_filtered, new_report = match_cohort(scenario_target, ordering, **kwargs)
            assert old_filtered == new_filtered
            assert _report_as_tuple(old_report) == _report_as_tuple(new_report)


def test_match_cohort_equals_the_actual_pre_m23_implementation_with_divergent_duplicates() -> None:
    """`match_cohort` itself (old or new) never runs the
    quarantine — for a pull-tie between two representations that disagree
    by value, both implementations keep picking by arrival order, and they
    must keep agreeing WITH EACH OTHER in each order (proof the refactor
    didn't change `match_cohort`'s own, still order-dependent, behaviour;
    only the new production `run_analysis` path — via quarantine — no
    longer depends on that order, see the run_analysis tests below).
    """
    old_match_cohort = _load_pre_m23_match_cohort()
    target = _cm_target()
    divergent_a = _cm_log(name="Dup", report_code="TIEPULL", percentile=50.0, tier_pieces=0)
    divergent_b = _cm_log(name="Dup", report_code="TIEPULL", percentile=50.0, tier_pieces=4)
    # same tie key (report/fight/identity/priority), different value
    assert divergent_a != divergent_b

    for ordering in ([divergent_a, divergent_b], [divergent_b, divergent_a]):
        old_filtered, old_report = old_match_cohort(target, ordering, min_n=1)
        new_filtered, new_report = match_cohort(target, ordering, min_n=1)
        assert old_filtered == new_filtered == [ordering[0]]  # first-arrival wins, both versions
        assert _report_as_tuple(old_report) == _report_as_tuple(new_report)


# --- duplicate-conflict quarantine (docs/methodology.md §5.2) -------------------


def test_quarantine_removes_a_divergent_group_entirely() -> None:
    """Hand-written oracle: two representations of the same observation
    (same pull, same player), different by value (`!=`) — both excluded,
    none chosen.
    """
    target = _cm_target()
    divergent_a = _cm_log(name="Dup", report_code="TIEPULL", percentile=50.0, tier_pieces=0)
    divergent_b = _cm_log(name="Dup", report_code="TIEPULL", percentile=50.0, tier_pieces=4)
    clean = _cm_log(name="Clean", report_code="CLEANPULL", percentile=60.0)

    for ordering in (
        [divergent_a, divergent_b, clean],
        [divergent_b, divergent_a, clean],
        [clean, divergent_a, divergent_b],
    ):
        output, report = quarantine_conflicting_duplicates(target, ordering)
        assert output == [clean]
        assert report.n_input == 3
        assert report.n_output == 1
        assert report.excluded_logs == 2
        assert report.conflicting_ids == tuple(
            sorted({damage_reference_id(divergent_a), damage_reference_id(divergent_b)})
        )


def test_quarantine_leaves_a_group_of_identical_duplicates_intact() -> None:
    """Two BYTE-IDENTICAL representations (same observation, equal by `==`)
    are not a conflict — the hygiene `min()` already collapses them to the
    same value regardless of which one it picks.
    """
    target = _cm_target()
    identical_a = _cm_log(name="Dup", report_code="SAMEPULL", percentile=70.0)
    identical_b = _cm_log(name="Dup", report_code="SAMEPULL", percentile=70.0)
    assert identical_a == identical_b

    output, report = quarantine_conflicting_duplicates(target, [identical_a, identical_b])
    assert output == [identical_a, identical_b]
    assert report.n_output == 2
    assert report.excluded_logs == 0
    assert report.conflicting_ids == ()


def test_quarantine_leaves_same_pull_different_percentile_intact() -> None:
    """Two DIFFERENT players on the same pull with different percentiles —
    different `player_identity`, so a different observation; not a
    conflict. The hygiene pull-dedup step decides between them, same as
    before M2.3.
    """
    target = _cm_target()
    low = _cm_log(name="PullLow", report_code="SAMEPULL", percentile=20.0)
    high = _cm_log(name="PullHigh", report_code="SAMEPULL", percentile=90.0)

    output, report = quarantine_conflicting_duplicates(target, [low, high])
    assert output == [low, high]
    assert report.excluded_logs == 0
    assert report.conflicting_ids == ()


def test_quarantine_same_player_same_pull_different_percentile_is_a_conflict() -> None:
    """The SAME player on the SAME pull with a different percentile (and
    every other field equal) is a conflict — `dedup_priority` (which embeds
    the percentile) is not part of the grouping key. Both representations
    are removed; neither's percentile is preferred.
    """
    target = _cm_target()
    low = _cm_log(name="Dup", report_code="SAMEPULL", percentile=20.0)
    high = _cm_log(name="Dup", report_code="SAMEPULL", percentile=90.0)
    assert low != high

    output, report = quarantine_conflicting_duplicates(target, [low, high])
    assert output == []
    assert report.excluded_logs == 2
    assert report.conflicting_ids == (damage_reference_id(low),)


def test_quarantine_domain_excludes_self_and_non_kill() -> None:
    """Self-references and non-kills sit outside the §4.0 domain — even a
    divergent self/non-kill pair passes through untouched here; hygiene
    excludes them afterwards with its own counters, never double-counted.
    """
    target = _cm_target()
    self_a = _cm_log(name=target.build.character_name, report_code="SELFPULL", tier_pieces=0)
    self_b = _cm_log(name=target.build.character_name, report_code="SELFPULL", tier_pieces=4)
    non_kill_a = _cm_log(name="Dead", report_code="DEADPULL", kill=False, tier_pieces=0)
    non_kill_b = _cm_log(name="Dead", report_code="DEADPULL", kill=False, tier_pieces=4)
    clean = _cm_log(name="Clean", report_code="CLEANPULL")

    output, report = quarantine_conflicting_duplicates(
        target, [self_a, self_b, non_kill_a, non_kill_b, clean]
    )
    assert output == [self_a, self_b, non_kill_a, non_kill_b, clean]
    assert report.excluded_logs == 0
    assert report.conflicting_ids == ()


def test_quarantine_conflicting_ids_are_ordered_and_deduplicated() -> None:
    """Three-way tie, all mutually divergent: every id appears once, sorted."""
    target = _cm_target()
    a = _cm_log(name="Zulu", report_code="TIEPULL", percentile=50.0, tier_pieces=0)
    b = _cm_log(name="Zulu", report_code="TIEPULL", percentile=50.0, tier_pieces=1)
    c = _cm_log(name="Zulu", report_code="TIEPULL", percentile=50.0, tier_pieces=2)

    output, report = quarantine_conflicting_duplicates(target, [a, b, c])
    assert output == []
    expected_ids = tuple(
        sorted({damage_reference_id(a), damage_reference_id(b), damage_reference_id(c)})
    )
    assert report.conflicting_ids == expected_ids
    assert len(report.conflicting_ids) == len(set(report.conflicting_ids))


def test_quarantine_is_invariant_to_permutation_as_a_multiset_and_report() -> None:
    """Conflict membership depends only on the
    multiset of candidates, never on their order — a fixed small input
    quarantines the same ids, and the survivors are the same objects
    (as a set, regardless of relative order), under every permutation.
    """
    target = _cm_target()
    divergent_a = _cm_log(name="Dup", report_code="TIEPULL", percentile=50.0, tier_pieces=0)
    divergent_b = _cm_log(name="Dup", report_code="TIEPULL", percentile=50.0, tier_pieces=4)
    clean_low = _cm_log(name="PullLow", report_code="SAMEPULL", percentile=20.0)
    clean_high = _cm_log(name="PullHigh", report_code="SAMEPULL", percentile=90.0)
    candidates = [divergent_a, divergent_b, clean_low, clean_high]

    baseline_output, baseline_report = quarantine_conflicting_duplicates(target, candidates)

    for permutation in itertools.permutations(candidates):
        output, report = quarantine_conflicting_duplicates(target, list(permutation))
        assert {id(log) for log in output} == {id(log) for log in baseline_output}
        assert report.conflicting_ids == baseline_report.conflicting_ids
        assert report.n_output == baseline_report.n_output
        assert report.excluded_logs == baseline_report.excluded_logs


def test_quarantine_removes_a_composite_observation_entirely() -> None:
    """Three representations of `REPORT:501:Duplicate`, same player/pull —
    A and B share a percentile but differ elsewhere, and C shares B's other
    fields but has a different percentile (a different `dedup_priority`).
    Grouping by the hygiene tie key would let C escape A/B's group and
    carry the id downstream; grouping by the observation makes all three
    one conflicting group, removed together, in every order.
    """
    target = _cm_target()
    a = _cm_log(
        name="Duplicate", report_code="REPORT", fight_id=501, percentile=50.0, tier_pieces=0
    )
    b = _cm_log(
        name="Duplicate", report_code="REPORT", fight_id=501, percentile=50.0, tier_pieces=4
    )
    c = _cm_log(
        name="Duplicate", report_code="REPORT", fight_id=501, percentile=99.0, tier_pieces=4
    )
    assert a != b
    assert b != c
    assert a != c
    assert damage_reference_id(a) == damage_reference_id(b) == damage_reference_id(c)

    for ordering in itertools.permutations([a, b, c]):
        output, report = quarantine_conflicting_duplicates(target, list(ordering))
        assert output == []
        assert report.n_input == 3
        assert report.excluded_logs == 3
        assert report.conflicting_ids == ("REPORT:501:Duplicate",)


def test_quarantine_closes_exclusion_by_id_across_different_servers() -> None:
    """Exclusion is closed by id: a homonym on a DIFFERENT server
    in the same pull is a separate observation (`player_identity` includes
    `server`) but shares the same `damage_reference_id` (which does not).
    Once the first observation is quarantined for internal conflict, the
    homonym is removed too, even though its own single representation is
    internally consistent — the id itself is what's excluded, not just the
    conflicting instances.
    """
    target = _cm_target()
    divergent_a = _cm_log(name="Dup", report_code="TIEPULL", percentile=50.0, tier_pieces=0)
    divergent_b = _cm_log(name="Dup", report_code="TIEPULL", percentile=50.0, tier_pieces=4)
    homonym_other_server = dataclasses.replace(
        divergent_a, build=dataclasses.replace(divergent_a.build, server="OtherServer")
    )
    assert damage_reference_id(homonym_other_server) == damage_reference_id(divergent_a)
    assert player_identity(homonym_other_server) != player_identity(divergent_a)

    for ordering in itertools.permutations([divergent_a, divergent_b, homonym_other_server]):
        output, report = quarantine_conflicting_duplicates(target, list(ordering))
        assert output == []
        assert report.excluded_logs == 3
        assert report.conflicting_ids == (damage_reference_id(divergent_a),)


def _duplicate_scenario_target_and_clean_refs() -> tuple[PlayerLog, list[PlayerLog]]:
    """Divergent-duplicate scenario setup: target Warlock/Demonology,
    duration 300, partition 4 (`_make_log`'s defaults), plus 16 clean
    references so the ledger/metrics stay genuinely SUFFICIENT and the
    quarantined id's absence is a positive check, not a vacuous one.
    """

    def make(name: str, **kwargs: Any) -> PlayerLog:
        return _make_log(
            character_name=name, class_name="Warlock", spec_name="Demonology", **kwargs
        )

    target = make("Target")
    clean_refs = [make(f"Clean{i}", fight_id=i + 2, tier_pieces=4) for i in range(16)]
    return target, clean_refs


def test_run_analysis_uptime_divergent_duplicate_quarantined_and_order_invariant(
    tmp_path: Path,
) -> None:
    """Same `REPORT:501:UptimeDuplicate`
    id, same class/spec/partition/duration/dedup priority (percentile is
    always None from `_make_log`, so `dedup_priority` ties on
    `(report_code, fight_id)` alone) — one with `uptime` absent, one
    `uptime=0.5`. Both fetch orders must now produce byte-identical
    provenance/JSON, with the id excluded from eligibility, the ledger and
    every metric population — never chosen by either representation.
    """
    target, clean_refs = _duplicate_scenario_target_and_clean_refs()
    missing = _make_log(
        character_name="UptimeDuplicate",
        class_name="Warlock",
        spec_name="Demonology",
        fight_id=501,
        uptime=None,
    )
    observed = _make_log(
        character_name="UptimeDuplicate",
        class_name="Warlock",
        spec_name="Demonology",
        fight_id=501,
        uptime=0.5,
    )
    assert missing != observed
    conflicting_id = damage_reference_id(missing)
    assert conflicting_id == "REPORT:501:UptimeDuplicate"

    forward, deps_forward = _run_analysis_with_fake_references(
        tmp_path / "forward", target, [*clean_refs, missing, observed]
    )
    reversed_result, deps_reversed = _run_analysis_with_fake_references(
        tmp_path / "reversed", target, [*clean_refs, observed, missing]
    )
    try:
        for result in (forward, reversed_result):
            provenance = result.comparability
            assert provenance is not None
            assert conflicting_id in provenance.hygiene.conflicting_duplicate_ids
            assert provenance.hygiene.excluded_conflicting_duplicates == 2
            assert conflicting_id not in provenance.eligibility.eligible_ids
            assert conflicting_id not in provenance.eligibility.indeterminate_ids
            assert conflicting_id not in provenance.eligibility.ineligible_ids
            assert conflicting_id not in provenance.ledger.member_ids
            assert provenance.ledger.state == "SUFFICIENT"  # the 16 clean refs are unaffected
            for summary in provenance.metrics.values():
                assert conflicting_id not in summary.descriptive.member_ids
                assert conflicting_id not in summary.aspirational.member_ids

        assert encode_comparability_provenance(
            forward.comparability
        ) == encode_comparability_provenance(reversed_result.comparability)
    finally:
        deps_forward.store.close()
        deps_reversed.store.close()


def test_run_analysis_class_divergent_duplicate_quarantined_and_order_invariant(
    tmp_path: Path,
) -> None:
    """Same id, divergent
    `class_name`/`spec_name` on the build — same fate as the uptime
    variant, in both orders.
    """
    target, clean_refs = _duplicate_scenario_target_and_clean_refs()
    class_match = _make_log(
        character_name="ClassDuplicate",
        class_name="Warlock",
        spec_name="Demonology",
        fight_id=502,
    )
    class_mismatch = dataclasses.replace(
        class_match,
        build=dataclasses.replace(class_match.build, class_name="Mage", spec_name="Fire"),
    )
    assert class_match != class_mismatch
    conflicting_id = damage_reference_id(class_match)

    forward, deps_forward = _run_analysis_with_fake_references(
        tmp_path / "forward", target, [*clean_refs, class_match, class_mismatch]
    )
    reversed_result, deps_reversed = _run_analysis_with_fake_references(
        tmp_path / "reversed", target, [*clean_refs, class_mismatch, class_match]
    )
    try:
        for result in (forward, reversed_result):
            provenance = result.comparability
            assert provenance is not None
            assert conflicting_id in provenance.hygiene.conflicting_duplicate_ids
            assert provenance.hygiene.excluded_conflicting_duplicates == 2
            assert conflicting_id not in provenance.eligibility.eligible_ids
            assert conflicting_id not in provenance.ledger.member_ids
            for summary in provenance.metrics.values():
                assert conflicting_id not in summary.descriptive.member_ids

        assert encode_comparability_provenance(
            forward.comparability
        ) == encode_comparability_provenance(reversed_result.comparability)
    finally:
        deps_forward.store.close()
        deps_reversed.store.close()


def test_run_analysis_identical_duplicate_stays_collapsed_by_hygiene_not_quarantine(
    tmp_path: Path,
) -> None:
    """Contrast case: two representations that agree on every field (same
    tie key, equal by `==`) are NOT a conflict — hygiene's own pull-dedup
    collapses them to one, exactly as before M2.3, and the id stays
    admitted in both fetch orders.
    """
    target, clean_refs = _duplicate_scenario_target_and_clean_refs()

    def make_identical() -> PlayerLog:
        return _make_log(
            character_name="IdenticalDuplicate",
            class_name="Warlock",
            spec_name="Demonology",
            fight_id=503,
        )

    identical_id = damage_reference_id(make_identical())

    forward, deps_forward = _run_analysis_with_fake_references(
        tmp_path / "forward", target, [*clean_refs, make_identical(), make_identical()]
    )
    reversed_result, deps_reversed = _run_analysis_with_fake_references(
        tmp_path / "reversed", target, [*clean_refs, make_identical(), make_identical()]
    )
    try:
        for result in (forward, reversed_result):
            provenance = result.comparability
            assert provenance is not None
            assert provenance.hygiene.excluded_conflicting_duplicates == 0
            assert identical_id not in provenance.hygiene.conflicting_duplicate_ids
            assert provenance.hygiene.deduped_pull == 1
            assert identical_id in provenance.eligibility.eligible_ids
            assert identical_id in provenance.ledger.member_ids

        assert encode_comparability_provenance(
            forward.comparability
        ) == encode_comparability_provenance(reversed_result.comparability)
    finally:
        deps_forward.store.close()
        deps_reversed.store.close()


def test_run_analysis_composite_observation_quarantined_in_every_permutation(
    tmp_path: Path,
) -> None:
    """The composite A/B/C observation (all `REPORT:501:Duplicate`) via real
    `run_analysis`, across all SIX permutations of the three fetch orders:
    grouping by the observation (not `dedup_priority`) and closing the
    exclusion by id removes all three together, regardless of order.

    Checked at every boundary the id could otherwise leak through:
    eligibility (eligible/indeterminate/ineligible), the ledger, every
    DESCRIPTIVE and ASPIRATIONAL population, the contract, the decoded
    manifest JSON, and the JSON read back from the `runs` row in the Store —
    byte-identical across all six permutations.
    """
    target, clean_refs = _duplicate_scenario_target_and_clean_refs()
    a = _make_log(
        character_name="Duplicate",
        class_name="Warlock",
        spec_name="Demonology",
        fight_id=501,
        uptime=None,
    )
    b = _make_log(
        character_name="Duplicate",
        class_name="Warlock",
        spec_name="Demonology",
        fight_id=501,
        uptime=0.5,
    )
    # C shares every field with B except a different percentile — a
    # different `dedup_priority` from A/B.
    c = dataclasses.replace(b, percentile=99.0)
    assert a != b
    assert b != c
    assert a != c
    conflicting_id = damage_reference_id(a)
    assert conflicting_id == "REPORT:501:Duplicate"

    results: list[Any] = []
    deps_list: list[Any] = []
    try:
        for index, permutation in enumerate(itertools.permutations([a, b, c])):
            result, deps = _run_analysis_with_fake_references(
                tmp_path / f"perm{index}", target, [*clean_refs, *permutation]
            )
            results.append(result)
            deps_list.append(deps)

            provenance = result.comparability
            assert provenance is not None
            assert provenance.hygiene.conflicting_duplicate_ids == (conflicting_id,)
            assert provenance.hygiene.excluded_conflicting_duplicates == 3
            assert conflicting_id not in provenance.eligibility.eligible_ids
            assert conflicting_id not in provenance.eligibility.indeterminate_ids
            assert conflicting_id not in provenance.eligibility.ineligible_ids
            assert conflicting_id not in provenance.ledger.member_ids
            assert provenance.ledger.state == "SUFFICIENT"  # the 16 clean refs are unaffected
            for summary in provenance.metrics.values():
                assert conflicting_id not in summary.descriptive.member_ids
                assert conflicting_id not in summary.aspirational.member_ids

            contract = build_report_contract(result, setup=result.setup_analysis)
            assert contract.confianca.comparability is provenance
            assert result.manifest.comparability_provenance_json is not None
            decoded_manifest = decode_comparability_provenance(
                result.manifest.comparability_provenance_json
            )
            assert decoded_manifest == provenance
            cohort_id = result.manifest.cohort_id
            row = deps.store.query(
                f"SELECT comparability_provenance_json FROM runs WHERE cohort_id='{cohort_id}'"
            ).row(-1)
            assert decode_comparability_provenance(row[0]) == provenance

        baseline_json = encode_comparability_provenance(results[0].comparability)
        for result in results[1:]:
            assert encode_comparability_provenance(result.comparability) == baseline_json
    finally:
        for deps in deps_list:
            deps.store.close()


# --- §5/AC1: population routing — M2.1/M2.2 pure composition -------------------


def _stage(
    target: PlayerLog, candidates: list[PlayerLog], *, min_n: int = COHORT_MIN_HARD
) -> tuple[
    DuplicateConflictReport,
    HygieneReport,
    ReferenceEligibilityPopulation,
    list[PlayerLog],
    MatchReport,
    Mapping[str, MetricPopulationSet],
]:
    """Mirrors the production stage order in `pipeline.py`, including the
    §4.0 quarantine that now runs before hygiene.
    """
    quarantined, quarantine_report = quarantine_conflicting_duplicates(target, candidates)
    hygienic, hygiene_report = hygienic_candidates(target, quarantined)
    eligibility = evaluate_references(target, hygienic)
    eligible_logs = [
        log for log in hygienic if damage_reference_id(log) in eligibility.eligible_ids
    ]
    ledger, match_report = match_covariates(target, eligible_logs, min_n=min_n)
    populations = select_metric_populations(target, hygienic, eligibility=eligibility, catalog=None)
    return quarantine_report, hygiene_report, eligibility, ledger, match_report, populations


def test_class_mismatched_reference_stays_out_of_ledger_and_every_metric_population() -> None:
    """AC1/AC3: match_covariates never checks class_name/spec_name (its own
    docstring: exact-matched upstream by the ranking query, never checked
    here) — so a reference with a mismatched class that somehow reached
    fetch_cohort_logs would have silently entered R_log before M2.3. M2.1
    now excludes it (CLASS_MISMATCH) before match_covariates ever runs.

    `compatible` explicitly shares the target's class/spec (the factory
    default is Mage/Fire), so the test proves positive selection alongside
    the exclusion instead of passing on vacuously empty populations.
    """
    target = _cohort_log("Target", class_name="Warlock", spec_name="Demonology")
    compatible = [
        _cohort_log(f"C{i}", fight_id=i + 2, class_name="Warlock", spec_name="Demonology")
        for i in range(9)
    ]
    wrong_class = _cohort_log("WrongClass", fight_id=200, class_name="Mage", spec_name="Fire")
    candidates = [*compatible, wrong_class]

    _quarantine, _hygiene_report, eligibility, ledger, _match_report, populations = _stage(
        target, candidates
    )
    wrong_id = damage_reference_id(wrong_class)
    compatible_ids = {damage_reference_id(log) for log in compatible}

    assert wrong_id in eligibility.ineligible_ids
    assert "CLASS_MISMATCH" in eligibility.excluded_reasons[wrong_id]
    assert compatible_ids <= set(eligibility.eligible_ids)  # positive selection: genuinely admitted
    assert wrong_id not in {damage_reference_id(log) for log in ledger}
    assert {damage_reference_id(log) for log in ledger} == compatible_ids  # ledger keeps them all
    for population_set in populations.values():
        assert wrong_id not in population_set.descriptive.members
        assert wrong_id not in population_set.aspirational.members
        assert compatible_ids <= set(population_set.descriptive.members)


def _mixed_population_fixture() -> tuple[PlayerLog, list[PlayerLog], PlayerLog]:
    """Shared populations-differ fixture: 30 clean references
    (COHORT_STRETCH_N-sized ledger; half omit
    complete damage collection, excluded from damage-derived metrics but
    not from the ledger or from metrics that don't need damage data), 4
    tier_pieces-mismatched references (excluded from the ledger — never
    relaxed once COHORT_TARGET_N is already reached — but tier_pieces is
    NOT_ADMITTED for aura_uptime_fraction/player_casts_per_minute, M2.2
    §5.3, so they stay IN those two metrics' populations), and one
    class-mismatched reference (excluded from everything by M2.1).
    """

    def make(name: str, **kwargs: Any) -> PlayerLog:
        return _make_log(
            character_name=name, class_name="Warlock", spec_name="Demonology", **kwargs
        )

    target = make("Target")
    references = [
        make(f"R{i}", fight_id=i + 2, damage=200_000.0, complete_damage_collection=i < 16)
        for i in range(30)
    ]
    references += [make(f"TierOff{i}", fight_id=100 + i, tier_pieces=0) for i in range(4)]
    wrong = make("WrongClass", fight_id=999)
    references.append(
        dataclasses.replace(wrong, build=dataclasses.replace(wrong.build, class_name="Mage"))
    )
    return target, references, wrong


def test_run_analysis_population_routing_larger_and_smaller_than_ledger_in_same_replay(
    tmp_path: Path,
) -> None:
    """AC1: a REAL run_analysis replay, at the
    production min_n (COHORT_TARGET_N via COHORT_STRETCH_N here), where at
    least one metric's DESCRIPTIVE population is LARGER than R_log
    (aura_uptime_fraction — tier_pieces NOT_ADMITTED) and at least one is
    SMALLER (gross_ability_dps — excludes incomplete damage collection) in
    the SAME replay, checked against the IDs/values MetricComparison, the
    contract and the render actually consume — not select_metric_populations
    in isolation.
    """
    target, references, wrong = _mixed_population_fixture()

    result, deps = _run_analysis_with_fake_references(tmp_path, target, references)
    try:
        provenance = result.comparability
        assert provenance is not None
        assert provenance.ledger.state == "SUFFICIENT"
        assert provenance.ledger.n == 30

        uptime_population = provenance.metrics["aura_uptime_fraction:1"]
        dps_population = provenance.metrics["gross_ability_dps:1"]
        assert uptime_population.descriptive.n == 34  # LARGER than the ledger (30)
        assert dps_population.descriptive.n == 16  # SMALLER than the ledger (30)

        # The IDs/values actually consumed by MetricComparison — not just
        # select_metric_populations in isolation.
        comparisons = result.dps_gap.metric_comparisons
        assert set(comparisons) == set(provenance.metrics)
        for metric_id, comparison in comparisons.items():
            expected_ids = provenance.metrics[metric_id].descriptive.member_ids
            assert comparison.reference_ids == expected_ids
            assert len(comparison.reference_values) == len(comparison.reference_ids)
        assert (
            comparisons["aura_uptime_fraction:1"].reference_ids
            != comparisons["gross_ability_dps:1"].reference_ids
        )

        wrong_id = damage_reference_id(wrong)
        assert wrong_id not in provenance.ledger.member_ids
        for summary in provenance.metrics.values():
            assert wrong_id not in summary.descriptive.member_ids

        # The M1 identity must be checked against
        # a result this fixture is EXPECTED to produce, not skipped if the
        # comparison happens to come back empty — assert the quantitative
        # case actually materialized before computing the identity over it.
        ledger_comparison = result.dps_gap.comparison
        assert ledger_comparison is not None
        assert result.dps_gap.accounting_status == "AVAILABLE"
        assert ledger_comparison.total_delta_dps is not None
        reconstructed = math.fsum(
            (
                *ledger_comparison.ability_delta_dps.values(),
                ledger_comparison.support_delta_dps or 0.0,
                ledger_comparison.residual_dps or 0.0,
            )
        )
        assert reconstructed == pytest.approx(ledger_comparison.total_delta_dps, abs=1e-6)

        contract = build_report_contract(result, setup=result.setup_analysis)
        assert contract.confianca.comparability is provenance

        cli_text, discord_text = _render_all(result, contract)
        assert "Traceback" not in cli_text
        # With a SUFFICIENT ledger and a real
        # quantitative comparison, the render must show the actual computed
        # gap — not just avoid crashing — the mirror-image check of the
        # insufficient-ledger tests, which assert its ABSENCE.
        assert "Comparação indisponível" not in cli_text
        assert "Gap observado" in cli_text
        # The Discord text is verified by CONTENT, not by the
        # absence of a Traceback. Here the ledger comparison IS computed, so the
        # published claim must carry the N that comparison actually used (its
        # accepted quantitative references), never the ledger-input N or a
        # metric population's N.
        assert "Traceback" not in discord_text
        accepted_n = ledger_comparison.reference_n
        assert accepted_n == 16
        assert f"dos {accepted_n} comparáveis" in discord_text
        for other_n in (provenance.ledger.n, uptime_population.descriptive.n):
            assert f"dos {other_n} comparáveis" not in discord_text
    finally:
        deps.store.close()


def test_run_analysis_consumer_n_before_and_after_m23_on_the_mixed_replay(tmp_path: Path) -> None:
    """AC6: the per-consumer "N antes/depois" table for
    the populations-differ replay, MEASURED — not narrated. "Antes" runs the real
    pre-M2.3 consumers (`match_cohort`, `compare_metrics`, `compare_damage`)
    loaded from the M2.2 closure commit on the same references, at production
    `min_n`/policy and with the same catalog; "depois" is what the current
    `run_analysis` actually used. The evidence table is this test's assertions.
    """
    target, references, _wrong = _mixed_population_fixture()
    old_match = _load_m22_closure_module(
        "m23_before_cohort_match", "src/botgitgud/analysis/cohort_match.py"
    )
    old_metrics = _load_m22_closure_module(
        "m23_before_metric_observations", "src/botgitgud/analysis/metric_observations.py"
    )
    old_measurement = _load_m22_closure_module(
        "m23_before_measurement", "src/botgitgud/analysis/measurement.py"
    )

    result, deps = _run_analysis_with_fake_references(tmp_path, target, references)
    try:
        before_ledger, _ = old_match.match_cohort(
            target,
            references,
            min_n=COHORT_TARGET_N,
            matching_policy_version=DEFAULT_MATCHING_POLICY_VERSION,
        )
        before_metrics = old_metrics.compare_metrics(target, before_ledger, deps.catalog)
        before_accounting = old_measurement.compare_damage(target, tuple(before_ledger))

        provenance = result.comparability
        assert provenance is not None
        after_accounting = result.dps_gap.comparison
        assert after_accounting is not None
        after_metrics = result.dps_gap.metric_comparisons

        rows = {
            "ledger_input": (len(before_ledger), provenance.ledger.n),
            "ledger_accepted_quantitative": (
                before_accounting.reference_n,
                after_accounting.reference_n,
            ),
            **{
                metric_id: (
                    len(comparison.reference_ids),
                    len(after_metrics[metric_id].reference_ids),
                )
                for metric_id, comparison in before_metrics.items()
            },
        }
        assert rows == {
            "ledger_input": (31, 30),
            "ledger_accepted_quantitative": (17, 16),
            "gross_ability_dps:1": (17, 16),
            "damage_events_per_second:1": (17, 16),
            "damage_per_event:1": (17, 16),
            "gross_damage_share_pct:1": (17, 16),
            "aura_uptime_fraction:1": (31, 34),
            "player_casts_per_minute:1": (31, 34),
        }
        # The "depois" column is the population each consumer truly consumed.
        for metric_id, comparison in after_metrics.items():
            assert comparison.reference_ids == provenance.metrics[metric_id].descriptive.member_ids

        # Why gross_ability_dps went 35 -> 16 (docs/methodology.md):
        # 14 references without complete damage collection, 4 outside the tier band,
        # 1 rejected by M2.1 (class) — 35 entries - 19 excluded = 16 members.
        exclusions = dict(provenance.metrics["gross_ability_dps:1"].descriptive.exclusion_counts)
        assert exclusions == {
            "BASIC_ELIGIBILITY_INELIGIBLE": 1,
            "TIER_PIECES_BAND_MISMATCH": 4,
            "UNKNOWN_DAMAGE_COLLECTION": 14,
        }
        assert len(references) - sum(exclusions.values()) == 16
    finally:
        deps.store.close()


def test_run_analysis_population_routing_is_invariant_to_reference_order(tmp_path: Path) -> None:
    """The permutation check for the
    populations-differ replay must go through real `run_analysis`
    provenance, not `_stage` — a permuted fetch order must not change
    which ids land in the ledger vs. in any metric's population, nor the
    encoded provenance.
    """
    target, references, _wrong = _mixed_population_fixture()

    forward, deps_forward = _run_analysis_with_fake_references(
        tmp_path / "forward", target, references
    )
    reversed_result, deps_reversed = _run_analysis_with_fake_references(
        tmp_path / "reversed", target, list(reversed(references))
    )
    try:
        assert forward.comparability is not None
        assert reversed_result.comparability is not None
        assert encode_comparability_provenance(
            forward.comparability
        ) == encode_comparability_provenance(reversed_result.comparability)
        for metric_id in forward.dps_gap.metric_comparisons:
            assert (
                forward.dps_gap.metric_comparisons[metric_id].reference_ids
                == reversed_result.dps_gap.metric_comparisons[metric_id].reference_ids
            )
    finally:
        deps_forward.store.close()
        deps_reversed.store.close()


# --- §6.3: aspirational-ledger guard --------------------------------------------


def test_run_analysis_aspirational_ledger_guard_never_passes_non_finite_dps_and_respects_the_floor(
    tmp_path: Path,
) -> None:
    """Exercises pipeline.py's OWN aspirational guard through a real
    run_analysis replay, so removing the production guard fails the test.
    Below REFERENCE_MIN_N orderable ledger members, the aspirational
    comparison is unavailable (`compare_damage(player, ())`); at or above
    it, `select_benchmark_reference` actually runs and every member it
    returns has a finite dps.
    """

    def make(name: str, **kwargs: Any) -> PlayerLog:
        kwargs.setdefault("tier_pieces", 4)
        return _make_log(
            character_name=name, class_name="Warlock", spec_name="Demonology", **kwargs
        )

    target = make("Target")

    def build_references(n_non_finite: int, n_finite: int) -> list[PlayerLog]:
        refs = [make(f"NonFinite{i}", fight_id=i + 2, dps=None) for i in range(n_non_finite)]
        refs += [make(f"Finite{i}", fight_id=100 + i, dps=1000.0 + i) for i in range(n_finite)]
        return refs

    below = build_references(3, 6)  # 6 orderable < REFERENCE_MIN_N (8)
    result_below, deps_below = _run_analysis_with_fake_references(tmp_path / "below", target, below)
    try:
        assert result_below.comparability is not None
        assert result_below.comparability.ledger.state == "SUFFICIENT"
        aspirational_below = result_below.dps_gap.aspirational_comparison
        assert aspirational_below is not None
        assert aspirational_below.reference_ids == ()
        limitations = result_below.comparability.ledger.aspirational_limitations
        assert "ASPIRATIONAL_UNAVAILABLE" in limitations
    finally:
        deps_below.store.close()

    above = build_references(3, 9)  # 9 orderable >= REFERENCE_MIN_N (8)
    result_above, deps_above = _run_analysis_with_fake_references(tmp_path / "above", target, above)
    try:
        assert result_above.comparability is not None
        assert result_above.comparability.ledger.state == "SUFFICIENT"
        aspirational_above = result_above.dps_gap.aspirational_comparison
        assert aspirational_above is not None
        assert aspirational_above.reference_ids != ()
        member_by_id = {damage_reference_id(log): log for log in above}
        for rid in aspirational_above.reference_ids:
            log = member_by_id[rid]
            assert log.dps is not None and math.isfinite(log.dps)
    finally:
        deps_above.store.close()


# --- §7/AC2: comparability-provenance-v1 round-trip -----------------------------


def _sample_provenance() -> ComparabilityProvenance:
    target = _cohort_log("Target", tier_pieces=4)
    candidates = [_cohort_log(f"R{i}", fight_id=i + 2, tier_pieces=4) for i in range(9)]
    quarantine_report, hygiene_report, eligibility, ledger, match_report, populations = _stage(
        target, candidates
    )
    comparison = compare_damage(target, tuple(ledger))
    return build_comparability_provenance(
        target_id=damage_reference_id(target),
        reference_eligibility_policy_version=eligibility.policy_version,
        metric_population_policy_version=METRIC_POPULATION_POLICY_VERSION,
        ledger_matching_policy_version=DEFAULT_MATCHING_POLICY_VERSION,
        hygiene=summarize_hygiene(hygiene_report, quarantine_report),
        eligibility=summarize_eligibility(eligibility),
        ledger=summarize_ledger(
            state="SUFFICIENT",
            members=ledger,
            match_report=match_report,
            accepted_ids=comparison.reference_ids,
            accounting_exclusions=comparison.excluded_references,
            aspirational_member_ids=(),
            aspirational_limitations=("ASPIRATIONAL_UNAVAILABLE",),
        ),
        metrics=summarize_metrics(populations),
    )


def test_comparability_provenance_round_trips_through_encode_decode() -> None:
    provenance = _sample_provenance()
    encoded = encode_comparability_provenance(provenance)
    decoded = decode_comparability_provenance(encoded)
    assert decoded == provenance
    # Canonical: sorted keys, compact separators, ASCII only.
    assert json.loads(encoded) == json.loads(json.dumps(json.loads(encoded), sort_keys=True))
    assert ", " not in encoded and ": " not in encoded
    assert all(ord(c) < 128 for c in encoded)


def test_decode_rejects_an_unknown_provenance_version() -> None:
    with pytest.raises(ValueError, match="unsupported comparability provenance version"):
        decode_comparability_provenance('{"provenance_version": "bogus-v0"}')


def test_comparability_provenance_round_trips_through_store_write_and_read(tmp_path: Path) -> None:
    provenance = _sample_provenance()
    encoded = encode_comparability_provenance(provenance)
    manifest = RunManifest(
        cohort_id="cohort-m23",
        code_version="test-code",
        generated_at=datetime.now(UTC),
        n_members=9,
        wcl_partition=4,
        settings_hash="hash",
        reference_eligibility_policy_version=REFERENCE_ELIGIBILITY_POLICY_VERSION,
        metric_population_policy_version=METRIC_POPULATION_POLICY_VERSION,
        ledger_matching_policy_version=DEFAULT_MATCHING_POLICY_VERSION,
        comparability_provenance_version=COMPARABILITY_PROVENANCE_VERSION,
        comparability_provenance_json=encoded,
    )
    with Store(tmp_path) as store:
        store.write_run(manifest)
        row = store.query(
            "SELECT reference_eligibility_policy_version, metric_population_policy_version, "
            "ledger_matching_policy_version, comparability_provenance_version, "
            "comparability_provenance_json FROM runs WHERE cohort_id='cohort-m23'"
        ).row(0)
    assert row[0] == REFERENCE_ELIGIBILITY_POLICY_VERSION
    assert row[1] == METRIC_POPULATION_POLICY_VERSION
    assert row[2] == DEFAULT_MATCHING_POLICY_VERSION
    assert row[3] == COMPARABILITY_PROVENANCE_VERSION
    assert decode_comparability_provenance(row[4]) == provenance


def test_legacy_runs_row_decodes_with_unknown_comparability_version(tmp_path: Path) -> None:
    """A database created before M2.3 must still be readable, and
    its historical row must read the new columns as NULL/unknown — never a
    fabricated current-version value. Same additive-migration pattern as
    test_m1_persistence_contract.py's A19.
    """
    db = tmp_path / "warehouse.duckdb"
    with duckdb.connect(str(db)) as connection:
        connection.execute(
            "CREATE TABLE runs (cohort_id VARCHAR, code_version VARCHAR, "
            "generated_at TIMESTAMP, n_members INTEGER, wcl_partition INTEGER, "
            "settings_hash VARCHAR, measurement_input_version VARCHAR, "
            "damage_comparison_version VARCHAR, reference_n_quantitative INTEGER)"
        )
        connection.execute(
            "INSERT INTO runs VALUES ('old-m1','old-code','2020-01-01',20,1,"
            "'old-settings','measurement-input-v1','damage-comparison-v2',12)"
        )
    with Store(tmp_path) as store:
        row = store.query(
            "SELECT reference_eligibility_policy_version, metric_population_policy_version, "
            "ledger_matching_policy_version, comparability_provenance_version, "
            "comparability_provenance_json FROM runs WHERE cohort_id='old-m1'"
        ).row(0)
    assert row == (None, None, None, None, None)


# --- §6.2/§6.4: run_analysis integration — ledger insufficient ------------------


def test_run_analysis_ledger_insufficient_via_graphql_partition_fix_still_renders_safe(
    tmp_path: Path,
) -> None:
    """Extends test_pipeline.py's
    test_insufficient_ledger_after_covariate_matching_completes_with_ledger_state_insufficient
    through the full GraphQL-mocked path: setup_analysis stays present
    (RP.2: independent of the execution cohort), and both CLI and Discord
    rendering complete without exception and without a fabricated
    dependent-comparison number.

    The fixture includes the "report_rankings" response: without it the
    target's own `fight.partition` stays None and M2.1 would mark every
    reference PARTITION_UNKNOWN. With it, duration alone (30% off, outside
    ±20%) excludes every reference from the LEDGER via match_covariates,
    while eligibility itself succeeds.
    """
    meta = [_meta_response(class_name="Warlock", spec_name="Demonology")]
    events = [
        _events_response(
            [{"sourceID": 6, "type": "cast", "abilityGameID": 104316, "timestamp": 1300}]
        )
    ]
    percentile = [_percentile_response(71.0, PRIMARY_REPORT, PRIMARY_FIGHT)]
    rankings_entries = []
    for i in range(N_REFS):
        code = f"REFCODE{i:09d}"
        rankings_entries.append(
            {"name": f"Ref{i}", "duration": 130_000, "report": {"code": code, "fightID": 1}}
        )
        meta.append(
            _meta_response(
                player_id=100 + i,
                player_name=f"Ref{i}",
                class_name="Warlock",
                spec_name="Demonology",
                start=0,
                end=130_000,  # 30s off the 100s primary fight: outside ±20%
                damage_total=900_000.0,
            )
        )
        events.append(
            _events_response(
                [{"sourceID": 100 + i, "type": "cast", "abilityGameID": 104316, "timestamp": 1300}]
            )
        )
        percentile.append(_percentile_response(None, code, 1))

    responses = {
        "meta": meta,
        "events": events,
        "percentile": percentile,
        "rankings": [
            {
                "data": {
                    "worldData": {
                        "encounter": {
                            "characterRankings": {
                                "rankings": rankings_entries,
                                "hasMorePages": False,
                            }
                        }
                    }
                }
            }
        ],
        "partition": _zone_partitions_response(),
        # Matches _zone_partitions_response()'s default (3) — see
        # _happy_path_responses's own comment for why this is required.
        "report_rankings": _report_rankings_response(partition=3),
    }
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)

    result = run_analysis(_req(), deps)

    assert result.comparability is not None
    # Eligibility itself succeeds now (PARTITION axis matches) — the
    # references are excluded from the LEDGER by duration, not from M2.1.
    assert result.comparability.eligibility.n_evaluated == N_REFS
    assert len(result.comparability.eligibility.eligible_ids) == N_REFS
    assert result.comparability.ledger.state == "INSUFFICIENT_REFERENCES"
    assert result.setup_analysis is not None  # RP.2: independent of execution cohort

    # Same as the direct-replay sibling test below: the "not computed"
    # fields of the insufficiency contract, asserted directly.
    assert result.performance is None
    assert result.comparisons == ()
    assert result.core_abilities == ()
    assert result.proc_analysis is None
    assert result.external_dps_context == ()

    contract = build_report_contract(result, setup=result.setup_analysis)
    assert contract.confianca.comparability is result.comparability
    assert contract.execucao.performance is None
    assert contract.execucao.comparisons == ()
    assert contract.core_abilities == ()

    cli_text, discord_text = _render_all(result, contract)
    assert "Traceback" not in cli_text
    # Absence of the comparison numbers those fields would have produced —
    # not just that rendering didn't crash.
    assert "ANALISE POR HABILIDADE" not in cli_text
    assert "SELF BUFFS & PROCS" not in cli_text
    assert "TIMELINE OFENSIVA" not in cli_text
    assert "CONTEXTO DE DPS EXTERNO" not in cli_text

    # Semantic Discord check — no comparison number, and the renderer's
    # own "no sustained priority" answer, not merely "no Traceback".
    assert "Traceback" not in discord_text
    _assert_no_uncomputed_comparison_numbers(discord_text)
    assert discord_text == _NO_PRIORITY_DISCORD_TEXT


def test_run_analysis_ledger_insufficient_with_sufficient_metric(tmp_path: Path) -> None:
    """The test above shows the ledger going
    INSUFFICIENT through the real GraphQL path but with equally-empty
    metric data (this fixture never populates damage/uptime data); this
    one demonstrates the contracted insufficiency scenario — ledger
    INSUFFICIENT_REFERENCES (every reference exceeds the ledger's maximum
    duration relaxation band, cohort_match.DURATION_BANDS_PCT's ±20%
    ceiling) while a metric whose own M2.2 duration handling doesn't
    exclude the same references (gross_ability_dps) stays
    SUFFICIENT_FOR_GRADING and keeps a real comparison — through a real
    run_analysis replay.
    """

    def make(name: str, **kwargs: Any) -> PlayerLog:
        return _make_log(
            character_name=name, class_name="Warlock", spec_name="Demonology", **kwargs
        )

    target = make("Target")
    references = [make(f"Long{i}", fight_id=i + 2, duration_s=390.0) for i in range(16)]

    result, deps = _run_analysis_with_fake_references(tmp_path, target, references)
    try:
        assert result.comparability is not None
        assert result.comparability.ledger.state == "INSUFFICIENT_REFERENCES"
        assert result.comparability.ledger.n == 0
        assert result.dps_gap.accounting_status == "NO_REFERENCES"
        assert result.setup_analysis is not None  # RP.2: independent of execution cohort

        sufficient_metric = result.comparability.metrics["gross_ability_dps:1"]
        assert sufficient_metric.descriptive.sufficiency == "SUFFICIENT_FOR_GRADING"
        assert sufficient_metric.descriptive.n > 0
        comparison = result.dps_gap.metric_comparisons["gross_ability_dps:1"]
        assert comparison.finding is not None  # the sufficient metric keeps its comparison
        assert comparison.reference_ids == sufficient_metric.descriptive.member_ids
        assert comparison.reference_ids  # non-empty despite the empty ledger

        # The "not computed" fields of the insufficiency contract
        # (docs/methodology.md §5.4), field by field — every one of these depends on R_log and
        # must stay absent, never a fabricated value, when the ledger is
        # empty. Asserted directly on AnalysisResult, not inferred from
        # rendering succeeding.
        assert result.performance is None
        assert result.comparisons == ()
        assert result.core_abilities == ()
        assert result.proc_analysis is None
        assert result.external_dps_context == ()
        assert result.dps_gap.aspirational_comparison is not None
        assert result.dps_gap.aspirational_comparison.reference_ids == ()

        contract = build_report_contract(result, setup=result.setup_analysis)
        assert contract.confianca.comparability is result.comparability
        # Same absence mirrored on the contract the renderers actually consume.
        assert contract.execucao.performance is None
        assert contract.execucao.comparisons == ()
        assert contract.core_abilities == ()
        assert contract.proc_analysis is None
        assert contract.external_dps_context == ()

        cli_text, discord_text = _render_all(result, contract)
        assert "Traceback" not in cli_text
        # Verify ABSENCE of the comparison numbers
        # these fields would have produced, not just that rendering didn't
        # crash — each header below only appears when its field is
        # non-empty (report/text.py's render_report), so a regression that
        # fabricated a value for any of them would make this fail.
        assert "ANALISE POR HABILIDADE" not in cli_text
        assert "SELF BUFFS & PROCS" not in cli_text
        assert "TIMELINE OFENSIVA" not in cli_text
        assert "CONTEXTO DE DPS EXTERNO" not in cli_text
        # The ledger-dependent comparison itself renders as explicitly
        # unavailable, never a fabricated DPS delta.
        assert "Comparação indisponível" in cli_text
        assert "NO_REFERENCES" in cli_text

        # The sufficient metric legitimately keeps its comparison, but the
        # ledger comparison it does NOT have (NO_REFERENCES) must not be
        # published by Discord: no median/gap/delta/reference numbers, and the
        # renderer's own "no sustained priority" answer.
        assert "Traceback" not in discord_text
        _assert_no_uncomputed_comparison_numbers(discord_text)
        assert discord_text == _NO_PRIORITY_DISCORD_TEXT
    finally:
        deps.store.close()


# --- run_analysis integration — sufficient ledger: comparability populated -----


def test_run_analysis_sufficient_ledger_populates_full_comparability_provenance(
    tmp_path: Path,
) -> None:
    transport = _DispatchTransport(_happy_path_responses())
    deps = _build_deps(tmp_path, transport)

    result = run_analysis(_req(), deps)

    assert result.comparability is not None
    c = result.comparability
    assert c.provenance_version == COMPARABILITY_PROVENANCE_VERSION
    assert c.reference_eligibility_policy_version == REFERENCE_ELIGIBILITY_POLICY_VERSION
    assert c.metric_population_policy_version == METRIC_POPULATION_POLICY_VERSION
    assert c.ledger.state == "SUFFICIENT"
    assert c.ledger.n == N_REFS
    assert set(c.ledger.member_ids) == set(c.eligibility.eligible_ids)
    assert c.ledger.member_ids == tuple(sorted(c.ledger.member_ids))  # ordered by reference_id
    assert c.metrics != {}
    for metric_id, summary in c.metrics.items():
        assert summary.metric_id == metric_id
        assert set(summary.aspirational.member_ids) <= set(summary.descriptive.member_ids)

    # Manifest and Store both received the same encoded provenance.
    assert result.manifest.comparability_provenance_version == COMPARABILITY_PROVENANCE_VERSION
    assert result.manifest.comparability_provenance_json is not None
    assert decode_comparability_provenance(result.manifest.comparability_provenance_json) == c

    cohort_id = result.manifest.cohort_id
    with deps.store as store:
        row = store.query(
            f"SELECT comparability_provenance_json FROM runs WHERE cohort_id='{cohort_id}'"
        ).row(-1)
    assert decode_comparability_provenance(row[0]) == c


def test_run_analysis_comparability_is_deterministic_across_repeated_runs(tmp_path: Path) -> None:
    """Same inputs, byte-identical AnalysisResult.comparability (docs/methodology.md) —
    two independent Store instances against the same cassette-like fixture.
    """
    transport_a = _DispatchTransport(_happy_path_responses())
    deps_a = _build_deps(tmp_path / "a", transport_a)
    result_a = run_analysis(_req(), deps_a)

    transport_b = _DispatchTransport(_happy_path_responses())
    deps_b = _build_deps(tmp_path / "b", transport_b)
    result_b = run_analysis(_req(), deps_b)

    assert result_a.comparability is not None
    assert result_b.comparability is not None
    assert result_a.comparability == result_b.comparability
    assert encode_comparability_provenance(
        result_a.comparability
    ) == encode_comparability_provenance(result_b.comparability)


# --- §8.1: metric_population.py / reference_eligibility.py stay byte-identical -


def test_m2_1_and_m2_2_modules_remain_byte_identical_to_the_closed_delivery() -> None:
    """SHA-256 of the git-committed (LF-normalized) blobs at the M2.1/M2.2
    closure commits — reference_eligibility.py at affd8653/edba6e62 (M2.1
    delivery/closure) and metric_population.py at the M2.2 closure. The
    working-copy hash is normalized to LF first: core.autocrlf materializes
    some checked-out files as CRLF on Windows, which is a line-ending
    artifact of checkout, never a content
    change — comparing raw working-copy bytes would make this test fail on
    a clean checkout that changed nothing.
    """
    repo_root = Path(__file__).resolve().parents[2]
    expected = {
        "src/botgitgud/analysis/reference_eligibility.py": (
            "17496bd431e1177dcc2bc9bf6430f506519c196416dd5c5df652deba406863ad"
        ),
        "src/botgitgud/analysis/metric_population.py": (
            "8aff6e01430c2e6fa7b0406ca42a0869f941f4b1186574bb40e80b3287aa5e2a"
        ),
    }
    for rel_path, sha in expected.items():
        content = (repo_root / rel_path).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(content).hexdigest() == sha, rel_path


def test_production_path_calls_compare_metrics_only_in_population_mode() -> None:
    """AC5/structural: dps_gap.py's production call site always threads
    metric_populations through — the legacy (no-population) branch of
    compare_metrics exists only for direct/legacy callers, never for the
    path run_analysis actually exercises.
    """
    source = (
        Path(__file__).resolve().parents[2] / "src" / "botgitgud" / "analysis" / "dps_gap.py"
    ).read_text(encoding="utf-8")
    assert "populations=metric_populations" in source
    # The unconditional legacy call (no populations= kwarg) must only exist
    # inside the explicit else-branch guarding metric_populations is None.
    assert "compare_metrics(player, references, catalog)" in source


# --- read-only replay of real metadata ------------------------------------------


def test_replay_gate1_scope_end_to_end_comparability_matches_manual_expectation() -> None:
    """Same fixture M2.1/M2.2 replayed: 20 real characters of one fight,
    Braska as target, Rohanlock the only M2.1-eligible reference. No log
    here carries damage_by_ability/cast_timeline/uptimes (only ranking
    metadata was captured), so every population lands at n=0 — the honest,
    calculated-by-hand result of this specific fixture (see
    docs/methodology.md for the identical M2.2-level census).
    """
    fixture_dir = Path(__file__).resolve().parents[1] / "fixtures" / "gate1_scope"
    before = snapshot_directory(fixture_dir)

    payload = json.loads((fixture_dir / "phase1_rankings.json").read_text(encoding="utf-8"))
    ranking = payload["response_json"]["data"]["reportData"]["report"]["rankings"]["data"][0]

    fight_id = ranking["fightID"]
    encounter_id = ranking["encounter"]["id"]
    boss_name = ranking["encounter"]["name"]
    difficulty = ranking["difficulty"]
    partition = ranking["partition"]
    kill = bool(ranking["kill"])
    duration_s = ranking["duration"] / 1000.0
    report_code = payload["variables"]["code"]
    role_map = {"tanks": "tank", "healers": "healer", "dps": "dps"}

    logs = []
    for role_key, role_block in ranking["roles"].items():
        for character in role_block["characters"]:
            fight = FightRef(
                report_code,
                fight_id,
                encounter_id,
                boss_name,
                difficulty,
                duration_s,
                kill,
                partition,
            )
            build = PlayerBuild(
                character["name"],
                character["server"]["name"],
                character["class"],
                character["spec"],
                role_map[role_key],  # type: ignore[arg-type]
                None,
                None,
                None,
            )
            logs.append(PlayerLog(fight, build, None, None, {}))

    target = next(log for log in logs if log.build.character_name == "Braska")
    candidates = tuple(log for log in logs if log.build.character_name != "Braska")

    quarantine_report, hygiene_report, eligibility, ledger, _match_report, populations = _stage(
        target, list(candidates)
    )
    assert quarantine_report.n_input == 19
    assert quarantine_report.excluded_logs == 0  # 19 distinct players, no tied/divergent pair
    assert hygiene_report.n_input == 19
    # All 19 references share the exact same (report_code, fight_id) — this
    # fixture is a single fight's full ranking, not 19 separate pulls — so
    # M2.3's pull-dedup (hygienic_candidates, new here: M2.1's own M2.1-era
    # replay called evaluate_references directly, without this stage)
    # collapses them to one representative before M2.1 ever runs. The
    # survivor is whichever passes dedup_priority's deterministic tie-break
    # (player_identity, since percentile/report/fight are tied for all 19).
    assert hygiene_report.deduped_pull == 18
    assert hygiene_report.n_output == 1
    assert eligibility.n_eligible in (0, 1)  # depends only on the one survivor's own identity
    assert len(ledger) == eligibility.n_eligible
    # No log here carries damage_by_ability/cast_timeline/uptimes (only
    # ranking metadata was captured) — Stage C (M1 metric availability) is
    # never AVAILABLE regardless of eligibility, so every population is
    # n=0 even in the (0,1) case where the lone survivor is itself eligible.
    for population_set in populations.values():
        assert population_set.descriptive.n == 0

    after = snapshot_directory(fixture_dir)
    assert diff_snapshots(before, after) == []
