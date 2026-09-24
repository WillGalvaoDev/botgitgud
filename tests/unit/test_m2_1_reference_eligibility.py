"""M2.1 — reference eligibility policy tests.

Matrix defined before implementation (SPEC §9.1-9.6, docs/m2-1-specification.md):
one case per closed reason code of §6, composite multi-axis cases, typed
abstentions, N-independence (isolated vs batch, permutation, size), absence of
repair by scarcity, determinism/canonical round-trip, a read-only replay of
real metadata (tests/fixtures/gate1_scope; data/raw has no Parquet files in this
workspace, declared explicitly rather than fabricated), and two structural
checks anchoring AC5 (import allowlist, via ast) and AC6 (the Ruff/Pyright
configuration the documented commands rely on, via tomllib) to the real
repository instead of prose. See docs/m2-1-review-evidence.md for the full
critério -> teste -> resultado table.
"""

from __future__ import annotations

import ast
import dataclasses
import itertools
import json
import math
import tomllib
from pathlib import Path
from typing import Literal

import pytest
from dir_snapshot import diff_snapshots, snapshot_directory
from real_corpus import CORPUS_ROOT, discover_corpus_paths

from botgitgud.analysis.cohort import COHORT_MIN_HARD
from botgitgud.analysis.measurement import damage_reference_id
from botgitgud.analysis.reference_eligibility import (
    REFERENCE_ELIGIBILITY_POLICY_VERSION,
    EligibilityAxis,
    EligibilityDecision,
    evaluate_reference,
    evaluate_references,
)
from botgitgud.domain.damage_scope import DamageScopeVersion
from botgitgud.domain.models import FightRef, PlayerBuild, PlayerLog

_DEFAULT_REPORT = "REPORT"
_DEFAULT_ENCOUNTER = 100
_DEFAULT_DIFFICULTY = 5
_DEFAULT_DURATION = 300.0
_DEFAULT_PARTITION = 4

_GATE1_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "gate1_scope"

_ROLE_MAP: dict[str, Literal["dps", "healer", "tank"]] = {
    "tanks": "tank",
    "healers": "healer",
    "dps": "dps",
}


def _make_log(
    *,
    fight_id: int = 1,
    character_name: str = "Reference",
    encounter_id: int = _DEFAULT_ENCOUNTER,
    difficulty: int = _DEFAULT_DIFFICULTY,
    duration_s: float = _DEFAULT_DURATION,
    kill: bool = True,
    partition: int | None = _DEFAULT_PARTITION,
    class_name: str = "Mage",
    spec_name: str = "Fire",
    damage_scope: DamageScopeVersion = DamageScopeVersion.WCL_TARGET_SCOPE_V1,
    report_code: str = _DEFAULT_REPORT,
) -> PlayerLog:
    fight = FightRef(
        report_code, fight_id, encounter_id, "Boss", difficulty, duration_s, kill, partition
    )
    build = PlayerBuild(character_name, None, class_name, spec_name, "dps", None, None, None)
    return PlayerLog(fight, build, None, None, {}, damage_scope=damage_scope)


def _target() -> PlayerLog:
    return _make_log(character_name="Target")


def _load_gate1_rankings_logs() -> list[PlayerLog]:
    payload = json.loads((_GATE1_FIXTURE_DIR / "phase1_rankings.json").read_text(encoding="utf-8"))
    ranking = payload["response_json"]["data"]["reportData"]["report"]["rankings"]["data"][0]
    fight_id = ranking["fightID"]
    encounter_id = ranking["encounter"]["id"]
    boss_name = ranking["encounter"]["name"]
    difficulty = ranking["difficulty"]
    partition = ranking["partition"]
    kill = bool(ranking["kill"])
    duration_s = ranking["duration"] / 1000.0
    report_code = payload["variables"]["code"]

    logs: list[PlayerLog] = []
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
                _ROLE_MAP[role_key],
                None,
                None,
                None,
            )
            logs.append(PlayerLog(fight, build, None, None, {}))
    return logs


# --- baseline: fully compatible reference -------------------------------


def test_fully_compatible_reference_is_eligible_with_only_hotfix_abstention() -> None:
    target = _target()
    reference = _make_log(character_name="Reference")
    result = evaluate_reference(target, reference)
    assert result.policy_version == REFERENCE_ELIGIBILITY_POLICY_VERSION
    assert result.decision is EligibilityDecision.ELIGIBLE
    assert result.reasons == ("HOTFIX_NOT_OBSERVABLE",)
    assert [axis.axis for axis in result.axes] == list(EligibilityAxis)
    assert result.reference_id == damage_reference_id(reference)


# --- IDENTITY axis -------------------------------------------------------


def test_self_reference_is_ineligible() -> None:
    target = _target()
    same_identity = _make_log(character_name="Target")
    result = evaluate_reference(target, same_identity)
    identity_axis = result.axes[0]
    assert identity_axis.axis is EligibilityAxis.IDENTITY
    assert "SELF_REFERENCE" in identity_axis.reasons
    assert identity_axis.decision is EligibilityDecision.INELIGIBLE
    assert result.decision is EligibilityDecision.INELIGIBLE


def test_encounter_mismatch_is_ineligible() -> None:
    target = _target()
    reference = _make_log(character_name="Reference", encounter_id=_DEFAULT_ENCOUNTER + 1)
    result = evaluate_reference(target, reference)
    identity_axis = result.axes[0]
    assert identity_axis.reasons == ("ENCOUNTER_MISMATCH",)
    assert identity_axis.decision is EligibilityDecision.INELIGIBLE
    assert result.decision is EligibilityDecision.INELIGIBLE


def test_difficulty_mismatch_is_ineligible() -> None:
    target = _target()
    reference = _make_log(character_name="Reference", difficulty=_DEFAULT_DIFFICULTY + 1)
    result = evaluate_reference(target, reference)
    identity_axis = result.axes[0]
    assert identity_axis.reasons == ("DIFFICULTY_MISMATCH",)
    assert result.decision is EligibilityDecision.INELIGIBLE


def test_class_mismatch_is_ineligible() -> None:
    target = _target()
    reference = _make_log(character_name="Reference", class_name="Warrior")
    result = evaluate_reference(target, reference)
    identity_axis = result.axes[0]
    assert identity_axis.reasons == ("CLASS_MISMATCH",)
    assert result.decision is EligibilityDecision.INELIGIBLE


def test_spec_mismatch_is_ineligible() -> None:
    target = _target()
    reference = _make_log(character_name="Reference", spec_name="Frost")
    result = evaluate_reference(target, reference)
    identity_axis = result.axes[0]
    assert identity_axis.reasons == ("SPEC_MISMATCH",)
    assert result.decision is EligibilityDecision.INELIGIBLE


def test_blank_class_name_is_identity_unknown_and_never_promoted_to_eligible() -> None:
    target = _target()
    reference = _make_log(character_name="Reference", class_name="   ")
    result = evaluate_reference(target, reference)
    identity_axis = result.axes[0]
    assert identity_axis.reasons == ("IDENTITY_UNKNOWN",)
    assert identity_axis.decision is EligibilityDecision.INDETERMINATE
    assert result.decision is EligibilityDecision.INDETERMINATE


def test_blank_spec_name_on_target_side_is_identity_unknown() -> None:
    target = _make_log(character_name="Target", spec_name="")
    reference = _make_log(character_name="Reference")
    result = evaluate_reference(target, reference)
    identity_axis = result.axes[0]
    assert identity_axis.reasons == ("IDENTITY_UNKNOWN",)
    assert identity_axis.decision is EligibilityDecision.INDETERMINATE


def test_class_and_spec_name_comparison_ignores_case_and_surrounding_whitespace() -> None:
    target = _target()
    reference = _make_log(character_name="Reference", class_name=" mage ", spec_name=" FIRE")
    result = evaluate_reference(target, reference)
    identity_axis = result.axes[0]
    assert identity_axis.decision is EligibilityDecision.ELIGIBLE
    assert identity_axis.reasons == ()
    assert identity_axis.observed_reference is not None
    assert "class_name=' mage '" in identity_axis.observed_reference
    assert "spec_name=' FIRE'" in identity_axis.observed_reference


# --- ATTEMPT_STATE axis ---------------------------------------------------


def test_reference_not_kill_is_ineligible_even_when_target_is_kill() -> None:
    target = _target()
    reference = _make_log(character_name="Reference", kill=False)
    result = evaluate_reference(target, reference)
    attempt_axis = result.axes[1]
    assert attempt_axis.axis is EligibilityAxis.ATTEMPT_STATE
    assert attempt_axis.reasons == ("ATTEMPT_STATE_NOT_KILL",)
    assert attempt_axis.observed_reference is not None
    assert "kill=False" in attempt_axis.observed_reference
    assert result.decision is EligibilityDecision.INELIGIBLE


@pytest.mark.parametrize("bad_duration", [0.0, -1.0, math.nan, math.inf])
def test_invalid_reference_duration_is_ineligible(bad_duration: float) -> None:
    target = _target()
    reference = _make_log(character_name="Reference", duration_s=bad_duration)
    result = evaluate_reference(target, reference)
    attempt_axis = result.axes[1]
    assert attempt_axis.reasons == ("INVALID_DURATION",)
    assert result.decision is EligibilityDecision.INELIGIBLE


@pytest.mark.parametrize("bad_duration", [0.0, -1.0, math.nan, math.inf])
def test_invalid_target_duration_is_ineligible(bad_duration: float) -> None:
    target = _make_log(character_name="Target", duration_s=bad_duration)
    reference = _make_log(character_name="Reference")
    result = evaluate_reference(target, reference)
    attempt_axis = result.axes[1]
    assert attempt_axis.reasons == ("INVALID_DURATION",)
    assert result.decision is EligibilityDecision.INELIGIBLE


# --- PARTITION axis --------------------------------------------------------


def test_partition_mismatch_is_ineligible() -> None:
    target = _target()
    reference = _make_log(character_name="Reference", partition=_DEFAULT_PARTITION + 1)
    result = evaluate_reference(target, reference)
    partition_axis = result.axes[2]
    assert partition_axis.axis is EligibilityAxis.PARTITION
    assert partition_axis.reasons == ("PARTITION_MISMATCH",)
    assert partition_axis.observed_player == str(_DEFAULT_PARTITION)
    assert partition_axis.observed_reference == str(_DEFAULT_PARTITION + 1)
    assert result.decision is EligibilityDecision.INELIGIBLE


def test_reference_partition_none_is_indeterminate_and_not_defaulted() -> None:
    target = _target()
    reference = _make_log(character_name="Reference", partition=None)
    result = evaluate_reference(target, reference)
    partition_axis = result.axes[2]
    assert partition_axis.reasons == ("PARTITION_UNKNOWN",)
    assert partition_axis.decision is EligibilityDecision.INDETERMINATE
    assert partition_axis.observed_reference is None
    assert partition_axis.observed_player == str(_DEFAULT_PARTITION)
    assert result.decision is EligibilityDecision.INDETERMINATE


def test_target_partition_none_is_indeterminate_and_not_defaulted_to_reference() -> None:
    target = _make_log(character_name="Target", partition=None)
    reference = _make_log(character_name="Reference")
    result = evaluate_reference(target, reference)
    partition_axis = result.axes[2]
    assert partition_axis.reasons == ("PARTITION_UNKNOWN",)
    assert partition_axis.observed_player is None
    assert partition_axis.observed_reference == str(_DEFAULT_PARTITION)


# --- DAMAGE_SCOPE axis ------------------------------------------------------


def test_reference_unreconciled_scope_is_ineligible() -> None:
    target = _target()
    reference = _make_log(character_name="Reference", damage_scope=DamageScopeVersion.UNRECONCILED)
    result = evaluate_reference(target, reference)
    scope_axis = result.axes[3]
    assert scope_axis.axis is EligibilityAxis.DAMAGE_SCOPE
    assert scope_axis.reasons == ("SCOPE_UNRECONCILED",)
    assert result.decision is EligibilityDecision.INELIGIBLE


def test_target_unreconciled_scope_is_ineligible() -> None:
    target = _make_log(character_name="Target", damage_scope=DamageScopeVersion.UNRECONCILED)
    reference = _make_log(character_name="Reference")
    result = evaluate_reference(target, reference)
    scope_axis = result.axes[3]
    assert scope_axis.reasons == ("SCOPE_UNRECONCILED",)
    assert result.decision is EligibilityDecision.INELIGIBLE


def test_scope_mismatch_between_legacy_and_v1_is_ineligible() -> None:
    target = _target()
    reference = _make_log(
        character_name="Reference", damage_scope=DamageScopeVersion.LEGACY_UNSCOPED
    )
    result = evaluate_reference(target, reference)
    scope_axis = result.axes[3]
    assert scope_axis.reasons == ("SCOPE_MISMATCH",)
    assert scope_axis.observed_player == DamageScopeVersion.WCL_TARGET_SCOPE_V1.value
    assert scope_axis.observed_reference == DamageScopeVersion.LEGACY_UNSCOPED.value
    assert result.decision is EligibilityDecision.INELIGIBLE


# --- HOTFIX axis -------------------------------------------------------------


def test_hotfix_axis_is_always_eligible_with_abstention_reason_regardless_of_other_axes() -> None:
    target = _target()
    reference = _make_log(character_name="Reference", class_name="Warrior")
    result = evaluate_reference(target, reference)
    hotfix_axis = result.axes[4]
    assert hotfix_axis.axis is EligibilityAxis.HOTFIX
    assert hotfix_axis.decision is EligibilityDecision.ELIGIBLE
    assert hotfix_axis.reasons == ("HOTFIX_NOT_OBSERVABLE",)
    assert hotfix_axis.observed_player is None
    assert hotfix_axis.observed_reference is None
    assert result.decision is EligibilityDecision.INELIGIBLE
    assert "HOTFIX_NOT_OBSERVABLE" in result.reasons


# --- composite / ordering -----------------------------------------------------


def test_composite_violation_orders_reasons_by_fixed_axis_order_then_alphabetically() -> None:
    target = _target()
    reference = _make_log(
        character_name="Reference",
        class_name="Warrior",
        encounter_id=_DEFAULT_ENCOUNTER + 1,
        partition=_DEFAULT_PARTITION + 1,
    )
    result = evaluate_reference(target, reference)
    assert result.reasons == (
        "CLASS_MISMATCH",
        "ENCOUNTER_MISMATCH",
        "PARTITION_MISMATCH",
        "HOTFIX_NOT_OBSERVABLE",
    )
    assert result.decision is EligibilityDecision.INELIGIBLE


# --- population-level declared limitations ------------------------------------


def test_target_not_kill_declares_limitation_without_excluding_kill_reference() -> None:
    target = _make_log(character_name="Target", kill=False)
    reference = _make_log(character_name="Reference", kill=True)
    population = evaluate_references(target, (reference,))
    assert "TARGET_ATTEMPT_NOT_KILL" in population.declared_limitations
    assert population.n_eligible == 1
    attempt_axis = population.results[0].axes[1]
    assert attempt_axis.decision is EligibilityDecision.ELIGIBLE


def test_hotfix_compatibility_unverified_present_only_when_some_reference_eligible() -> None:
    target = _target()
    ineligible_reference = _make_log(character_name="Reference", class_name="Warrior")
    population_none_eligible = evaluate_references(target, (ineligible_reference,))
    assert "HOTFIX_COMPATIBILITY_UNVERIFIED" not in population_none_eligible.declared_limitations
    assert population_none_eligible.n_eligible == 0

    eligible_reference = _make_log(character_name="Reference")
    population_some_eligible = evaluate_references(target, (eligible_reference,))
    assert "HOTFIX_COMPATIBILITY_UNVERIFIED" in population_some_eligible.declared_limitations


def test_insufficient_eligible_references_threshold_matches_cohort_min_hard() -> None:
    target = _target()
    below_threshold = tuple(_make_log(character_name=f"Ref{i}") for i in range(COHORT_MIN_HARD - 1))
    population_below = evaluate_references(target, below_threshold)
    assert population_below.n_eligible == COHORT_MIN_HARD - 1
    assert "INSUFFICIENT_ELIGIBLE_REFERENCES" in population_below.declared_limitations

    at_threshold = tuple(_make_log(character_name=f"Ref{i}") for i in range(COHORT_MIN_HARD))
    population_at = evaluate_references(target, at_threshold)
    assert population_at.n_eligible == COHORT_MIN_HARD
    assert "INSUFFICIENT_ELIGIBLE_REFERENCES" not in population_at.declared_limitations


def test_all_incompatible_population_reports_zero_eligible_without_promotion() -> None:
    target = _target()
    references = (
        _make_log(character_name="R1", class_name="Warrior"),
        _make_log(character_name="R2", partition=None),
        _make_log(character_name="R3", kill=False),
    )
    population = evaluate_references(target, references)
    assert population.n_eligible == 0
    assert population.eligible_ids == ()
    assert set(population.ineligible_ids) == {
        damage_reference_id(references[0]),
        damage_reference_id(references[2]),
    }
    assert population.indeterminate_ids == (damage_reference_id(references[1]),)
    assert "INSUFFICIENT_ELIGIBLE_REFERENCES" in population.declared_limitations


def test_evaluate_references_preserves_one_result_per_input_item_even_if_id_repeats() -> None:
    target = _target()
    reference = _make_log(character_name="Dup")
    population = evaluate_references(target, (reference, reference))
    assert len(population.results) == 2
    assert population.results[0] == population.results[1]


# --- independence of N (set, size, order) --------------------------------------


def _mixed_reference_set() -> tuple[PlayerLog, ...]:
    return (
        _make_log(character_name="R1"),
        _make_log(character_name="R2", class_name="Warrior"),
        _make_log(character_name="R3", partition=None),
        _make_log(character_name="R4", kill=False),
    )


def test_decision_is_invariant_to_isolated_vs_batch_evaluation() -> None:
    target = _target()
    references = _mixed_reference_set()
    isolated = tuple(evaluate_reference(target, r) for r in references)
    batched = evaluate_references(target, references).results
    assert isolated == batched


def test_decision_is_invariant_to_permutation_of_the_input_sequence() -> None:
    target = _target()
    references = list(_mixed_reference_set())
    baseline = {r.reference_id: r for r in evaluate_references(target, references).results}
    for permutation in itertools.permutations(references):
        population = evaluate_references(target, permutation)
        by_id = {r.reference_id: r for r in population.results}
        assert by_id == baseline


def test_decision_is_invariant_to_size_of_the_reference_set_including_one_incompatible() -> None:
    target = _target()
    incompatible = _make_log(character_name="Incompatible", class_name="Warrior")
    solo = evaluate_reference(target, incompatible)
    padding = tuple(_make_log(character_name=f"Pad{i}") for i in range(15))
    padded = evaluate_references(target, (incompatible, *padding))
    padded_result = next(r for r in padded.results if r.reference_id == solo.reference_id)
    assert padded_result == solo


# --- determinism / canonical round-trip -----------------------------------------


def test_repeated_evaluation_and_canonical_dict_round_trip_are_stable() -> None:
    target = _target()
    reference = _make_log(character_name="Reference", encounter_id=_DEFAULT_ENCOUNTER + 1)
    first = evaluate_reference(target, reference)
    second = evaluate_reference(target, reference)
    assert first == second

    canonical = json.loads(json.dumps(dataclasses.asdict(first), sort_keys=True))
    canonical_again = json.loads(json.dumps(dataclasses.asdict(second), sort_keys=True))
    assert canonical == canonical_again
    assert canonical["policy_version"] == REFERENCE_ELIGIBILITY_POLICY_VERSION
    assert canonical["decision"] == first.decision.value
    assert canonical["reasons"] == list(first.reasons)
    assert [axis["axis"] for axis in canonical["axes"]] == [a.axis.value for a in first.axes]


def test_population_repeated_evaluation_is_stable_via_canonical_dict() -> None:
    target = _target()
    references = (
        _make_log(character_name="R1"),
        _make_log(character_name="R2", class_name="Warrior"),
    )
    first = evaluate_references(target, references)
    second = evaluate_references(target, references)
    canonical_first = json.dumps(dataclasses.asdict(first), sort_keys=True)
    canonical_second = json.dumps(dataclasses.asdict(second), sort_keys=True)
    assert canonical_first == canonical_second


# --- read-only replay of real metadata ------------------------------------------


def test_replay_gate1_scope_rankings_census_matches_manual_expectation() -> None:
    """Read-only replay over the only real WCL metadata available in this
    workspace: tests/fixtures/gate1_scope/phase1_rankings.json, 20 characters
    of one real fight (same encounter/difficulty/partition/kill/duration for
    all 20, since it is a single fight). Expectation computed by hand from the
    fixture; recorded in docs/m2-1-review-evidence.md.
    """
    before = snapshot_directory(_GATE1_FIXTURE_DIR)
    logs = _load_gate1_rankings_logs()
    assert len(logs) == 20

    target = next(log for log in logs if log.build.character_name == "Braska")
    references = tuple(log for log in logs if log.build.character_name != "Braska")
    assert len(references) == 19

    population = evaluate_references(target, references)

    rohanlock = next(log for log in references if log.build.character_name == "Rohanlock")
    assert population.n_eligible == 1
    assert population.eligible_ids == (damage_reference_id(rohanlock),)
    assert len(population.ineligible_ids) == 18
    assert population.indeterminate_ids == ()
    assert set(population.declared_limitations) == {
        "HOTFIX_COMPATIBILITY_UNVERIFIED",
        "INSUFFICIENT_ELIGIBLE_REFERENCES",
    }

    after = snapshot_directory(_GATE1_FIXTURE_DIR)
    assert diff_snapshots(before, after) == []


def test_replay_over_data_raw_corpus_if_present() -> None:
    """SPEC §9.6: declare corpus absence explicitly rather than fabricate a
    number or silently skip without a message. This skip can never itself
    serve as passed acceptance proof for AC6 — the real M2.1 coverage proof
    in this suite is test_replay_gate1_scope_rankings_census_matches_manual_expectation.
    If a future environment provides the corpus, this exercises a genuine
    census instead of only skipping.
    """
    paths = discover_corpus_paths()
    if not paths:
        pytest.skip(
            f"corpus real ausente em {CORPUS_ROOT}; a prova de cobertura real "
            "do M2.1 nesta suíte é "
            "test_replay_gate1_scope_rankings_census_matches_manual_expectation "
            "(replay de tests/fixtures/gate1_scope); ver docs/m2-1-review-evidence.md §4"
        )
    from botgitgud.ingest.parquet_codec import read_parquet_log

    logs = [read_parquet_log(p) for p in paths]
    target, *references = logs
    population = evaluate_references(target, tuple(references))
    assert population.policy_version == REFERENCE_ELIGIBILITY_POLICY_VERSION
    assert len(population.results) == len(references)


# --- structural checks anchoring AC5 (imports) and AC6 (tooling config) --------


def test_reference_eligibility_module_imports_are_limited_to_the_declared_allowlist() -> None:
    # AC5 requires an automated node, not prose: parse the real file with ast
    # instead of hand-auditing it, so drift in the module's imports fails CI.
    module_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "botgitgud"
        / "analysis"
        / "reference_eligibility.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported_names.update(
                f"{module}.{alias.name}" if module else alias.name for alias in node.names
            )

    allowlist = {
        "__future__.annotations",
        "math",
        "collections.abc.Mapping",
        "collections.abc.Sequence",
        "dataclasses.dataclass",
        "enum.StrEnum",
        "botgitgud.analysis.cohort.COHORT_MIN_HARD",
        "botgitgud.analysis.measurement.damage_reference_id",
        "botgitgud.domain.damage_scope.DamageScopeVersion",
        "botgitgud.domain.models.PlayerLog",
    }
    assert imported_names == allowlist

    forbidden_prefixes = (
        "botgitgud.analysis.pipeline",
        "botgitgud.analysis.cohort_match",
        "botgitgud.report",
        "botgitgud.phase4",
        "botgitgud.bot",
        "botgitgud.cli",
    )
    assert not any(name.startswith(forbidden_prefixes) for name in imported_names)


def test_pyproject_declares_the_ruff_and_pyright_configuration_used_by_documented_commands() -> (
    None
):
    # AC6: anchors the commands documented in review-evidence.md §5 to the
    # real repository configuration, instead of citing the commands as proof.
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    config = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    assert set(config["tool"]["ruff"]["lint"]["select"]) == {
        "E",
        "F",
        "I",
        "UP",
        "B",
        "SIM",
        "RUF",
        "ANN",
        "PTH",
    }
    assert config["tool"]["pyright"]["typeCheckingMode"] == "standard"
    assert config["tool"]["pyright"]["venvPath"] == "."
    assert config["tool"]["pyright"]["venv"] == ".venv"
