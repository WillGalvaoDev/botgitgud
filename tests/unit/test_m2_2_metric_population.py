"""M2.2 — per-metric population selection tests.

Matrix defined before implementation (SPEC §12, docs/m2-2-specification.md):
covariate isolation per metric class, exact membership per contracted metric
(AC1), the relaxation ladder including the no-advance-past-floor rule and
zero-delta steps (AC2), invariance to M2.1 scarcity (AC3), descriptive vs.
aspirational separation including the outcome-ordering guard (AC4),
non-borrowing across metric_id (AC5), permutation invariance, absent-value
abstention (C06), the read-only B04 sensitivity artifact, a read-only replay
of real metadata (tests/fixtures/gate1_scope; data/raw has no Parquet files in
this workspace, declared explicitly rather than fabricated), and two
structural checks anchoring AC6 (import allowlist via ast; Ruff/Pyright
config via tomllib) to the real repository. See docs/m2-2-review-evidence.md
for the full critério -> teste -> resultado table.
"""

from __future__ import annotations

import ast
import dataclasses
import itertools
import json
import math
import tomllib
from pathlib import Path

import pytest
from dir_snapshot import diff_snapshots, snapshot_directory
from real_corpus import CORPUS_ROOT, discover_corpus_paths

from botgitgud.analysis.cohort_match import DURATION_BANDS_PCT
from botgitgud.analysis.grading import MIN_N_FOR_GRADING
from botgitgud.analysis.measurement import damage_reference_id
from botgitgud.analysis.metric_population import (
    METRIC_POPULATION_POLICY_VERSION,
    Covariate,
    MetricPopulationSet,
    PopulationKind,
    RelaxationRule,
    SensitivityRow,
    SufficiencyState,
    covariate_sensitivity,
    select_metric_population,
    select_metric_populations,
)
from botgitgud.analysis.reference_eligibility import evaluate_references
from botgitgud.domain.damage_scope import DamageScopeVersion
from botgitgud.domain.models import (
    AbilityDamage,
    CollectionProvenance,
    CollectionStatus,
    FightRef,
    MeasurementProvenance,
    PlayerBuild,
    PlayerLog,
)

# --- fixture construction ----------------------------------------------------

_SPELL_ID = 1
_DEFAULT_REPORT = "REPORT"
_DEFAULT_ENCOUNTER = 100
_DEFAULT_DIFFICULTY = 5
_DEFAULT_DURATION = 300.0
_DEFAULT_PARTITION = 4
_DEFAULT_ITEM_LEVEL = 400.0
_DEFAULT_TIER_PIECES = 4

_GATE1_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "gate1_scope"


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
    item_level: float | None = _DEFAULT_ITEM_LEVEL,
    tier_pieces: int | None = _DEFAULT_TIER_PIECES,
    external_buffs: frozenset[int] = frozenset(),
    has_augmentation: bool = False,
    dps: float | None = 1000.0,
    damage: float = 100_000.0,
    hits: int = 100,
    casts: int = 100,
    uptime: float | None = 0.5,
    complete_damage_collection: bool = True,
    complete_casts_collection: bool = True,
) -> PlayerLog:
    fight = FightRef(
        report_code, fight_id, encounter_id, "Boss", difficulty, duration_s, kill, partition
    )
    build = PlayerBuild(
        character_name,
        None,
        class_name,
        spec_name,
        "dps",
        item_level,
        None,
        tier_pieces,
        external_buffs=external_buffs,
        has_augmentation=has_augmentation,
    )
    ability = AbilityDamage(_SPELL_ID, damage, hits, casts)
    provenance = MeasurementProvenance(
        damage_collection=CollectionProvenance(
            CollectionStatus.COMPLETE if complete_damage_collection else CollectionStatus.UNKNOWN,
            () if complete_damage_collection else ("missing",),
            0,
            duration_s * 1000,
        ),
        casts_collection=CollectionProvenance(
            CollectionStatus.COMPLETE if complete_casts_collection else CollectionStatus.UNKNOWN,
            () if complete_casts_collection else ("missing",),
            0,
            duration_s * 1000,
        ),
        damage_reconciliation_status=damage_scope.value,
        damage_table_total=damage,
    )
    cast_timeline = {_SPELL_ID: (1.0,)} if casts else {}
    uptimes = {_SPELL_ID: uptime} if uptime is not None else {}
    return PlayerLog(
        fight=fight,
        build=build,
        dps=dps,
        percentile=None,
        cast_timeline=cast_timeline,
        damage_by_ability={_SPELL_ID: ability},
        uptimes=uptimes,
        damage_scope=damage_scope,
        measurement_provenance=provenance,
    )


def _target(**kwargs: object) -> PlayerLog:
    return _make_log(character_name="Target", **kwargs)  # type: ignore[arg-type]


def _refs(n: int, *, fight_id_start: int = 2, **kwargs: object) -> tuple[PlayerLog, ...]:
    return tuple(
        _make_log(character_name=f"Ref{fight_id_start}_{i}", fight_id=fight_id_start + i, **kwargs)  # type: ignore[arg-type]
        for i in range(n)
    )


def _populate(
    target: PlayerLog, references: tuple[PlayerLog, ...], *, metric: str = "gross_ability_dps"
) -> MetricPopulationSet:
    eligibility = evaluate_references(target, references)
    return select_metric_population(
        target, references, metric=metric, spell_id=_SPELL_ID, eligibility=eligibility, catalog=None
    )


def _strict_level_row(
    target: PlayerLog, references: tuple[PlayerLog, ...], *, metric: str
) -> SensitivityRow:
    """The covariate_sensitivity row at the strict level (index 0, before any
    relaxation) — used to isolate one covariate's effect on membership
    without the ladder's floor-seeking widening/dropping interfering.
    """
    eligibility = evaluate_references(target, references)
    rows = covariate_sensitivity(
        target, references, metric=metric, spell_id=_SPELL_ID, eligibility=eligibility, catalog=None
    )
    return rows[0]


_ALL_METRICS: tuple[str, ...] = (
    "gross_ability_dps",
    "damage_per_event",
    "damage_events_per_second",
    "player_casts_per_minute",
    "aura_uptime_fraction",
    "gross_damage_share_pct",
)
_AMPLITUDE_METRICS: tuple[str, ...] = (
    "gross_ability_dps",
    "damage_per_event",
    "damage_events_per_second",
    "gross_damage_share_pct",
)
_ILVL_TIER_NOT_ADMITTED_METRICS: tuple[str, ...] = (
    "player_casts_per_minute",
    "aura_uptime_fraction",
)


# --- baseline -----------------------------------------------------------------


def test_fully_compatible_references_are_all_members_with_only_declared_baseline_limitations() -> (
    None
):
    target = _target()
    references = _refs(9)
    population_set = _populate(target, references)
    descriptive = population_set.descriptive
    assert descriptive.policy_version == METRIC_POPULATION_POLICY_VERSION
    assert descriptive.kind is PopulationKind.DESCRIPTIVE
    assert descriptive.metric_id == "gross_ability_dps:1"
    assert descriptive.n == 9
    assert descriptive.members == tuple(sorted(damage_reference_id(r) for r in references))
    assert descriptive.sufficiency is SufficiencyState.SUFFICIENT_FOR_COMPARISON
    assert descriptive.relaxation_steps == ()
    assert descriptive.final_duration_band_pct == DURATION_BANDS_PCT[0]
    assert set(descriptive.declared_limitations) == {
        "HOTFIX_COMPATIBILITY_UNVERIFIED",
        "INSUFFICIENT_FOR_GRADING",
        "SETUP_NOT_MATCHED",
    }
    assert descriptive.excluded_reasons == {}
    assert set(descriptive.matched_covariates) == {
        Covariate.DURATION,
        Covariate.EXTERNAL_BUFFS,
        Covariate.ITEM_LEVEL,
        Covariate.TIER_PIECES,
    }


# --- covariate isolation: EXTERNAL_BUFFS (REQ, never relaxable) --------------


def test_external_buffs_mismatch_excludes_at_every_relaxation_level() -> None:
    # 10060 is a real member of EXTERNAL_OFFENSIVE_IDS (domain/external_buffs.py):
    # an id outside that closed set would intersect to an equally empty set on
    # both sides and never actually produce a mismatch.
    target = _target(external_buffs=frozenset({10060}))
    # Padding matches everything (including buffs), reaching the floor alone,
    # so `mismatched` (buffs only) stays excluded at every relaxation level.
    mismatched = _make_log(character_name="Mismatched", external_buffs=frozenset())
    padding = _refs(8, external_buffs=frozenset({10060}))
    population_set = _populate(target, (mismatched, *padding))
    rid = damage_reference_id(mismatched)
    assert rid not in population_set.descriptive.members
    assert population_set.descriptive.excluded_reasons[rid] == ("EXTERNAL_BUFFS_MISMATCH",)
    assert Covariate.EXTERNAL_BUFFS not in population_set.descriptive.relaxed_covariates


def test_external_buffs_is_required_and_non_relaxable_even_for_positional_metric() -> None:
    target = _target(external_buffs=frozenset({10060}))
    mismatched = _make_log(character_name="Mismatched", external_buffs=frozenset())
    population_set = _populate(target, (mismatched,), metric="player_casts_per_minute")
    rid = damage_reference_id(mismatched)
    assert rid not in population_set.descriptive.members
    assert population_set.descriptive.excluded_reasons[rid] == ("EXTERNAL_BUFFS_MISMATCH",)


# --- covariate isolation: ITEM_LEVEL / TIER_PIECES (REL, per matrix) ---------


def test_item_level_band_mismatch_excludes_amplitude_metric_at_strict_level() -> None:
    target = _target(item_level=400.0)
    mismatched = _make_log(character_name="Mismatched", item_level=200.0)
    # Padding matches every covariate and reaches the floor alone, so the
    # mismatched reference is never reached by relaxation.
    padding = _refs(8)
    population_set = _populate(target, (mismatched, *padding))
    rid = damage_reference_id(mismatched)
    assert population_set.descriptive.relaxation_steps == ()
    assert population_set.descriptive.excluded_reasons[rid] == ("ITEM_LEVEL_BAND_MISMATCH",)


def test_item_level_unknown_on_reference_excludes_with_closed_reason_not_a_default() -> None:
    target = _target(item_level=400.0)
    unknown = _make_log(character_name="Unknown", item_level=None)
    padding = _refs(8)
    population_set = _populate(target, (unknown, *padding))
    rid = damage_reference_id(unknown)
    assert population_set.descriptive.relaxation_steps == ()
    assert population_set.descriptive.excluded_reasons[rid] == ("ITEM_LEVEL_UNKNOWN",)


def test_tier_pieces_band_mismatch_excludes_amplitude_metric_at_strict_level() -> None:
    target = _target(tier_pieces=4)
    mismatched = _make_log(character_name="Mismatched", tier_pieces=0)
    padding = _refs(8)
    population_set = _populate(target, (mismatched, *padding))
    rid = damage_reference_id(mismatched)
    assert population_set.descriptive.relaxation_steps == ()
    assert population_set.descriptive.excluded_reasons[rid] == ("TIER_PIECES_BAND_MISMATCH",)


def test_tier_pieces_unknown_on_reference_excludes_with_closed_reason_not_a_default() -> None:
    target = _target(tier_pieces=4)
    unknown = _make_log(character_name="Unknown", tier_pieces=None)
    padding = _refs(8)
    population_set = _populate(target, (unknown, *padding))
    rid = damage_reference_id(unknown)
    assert population_set.descriptive.relaxation_steps == ()
    assert population_set.descriptive.excluded_reasons[rid] == ("TIER_PIECES_UNKNOWN",)


@pytest.mark.parametrize("metric", ["player_casts_per_minute", "aura_uptime_fraction"])
def test_item_level_and_tier_pieces_never_filter_the_two_not_admitted_metrics(metric: str) -> None:
    target = _target(item_level=400.0, tier_pieces=4)
    wildly_different = _make_log(character_name="Wild", item_level=1.0, tier_pieces=0)
    population_set = _populate(target, (wildly_different,), metric=metric)
    rid = damage_reference_id(wildly_different)
    assert rid in population_set.descriptive.members
    assert Covariate.ITEM_LEVEL in population_set.descriptive.declared_covariates
    assert Covariate.TIER_PIECES in population_set.descriptive.declared_covariates
    assert Covariate.ITEM_LEVEL not in population_set.descriptive.matched_covariates
    assert Covariate.ITEM_LEVEL not in population_set.descriptive.relaxed_covariates


def test_target_item_level_unknown_makes_covariate_inadmissible_never_a_filter() -> None:
    target = _target(item_level=None)
    reference = _make_log(character_name="AnyIlvl", item_level=1234.0)
    population_set = _populate(target, (reference,))
    rid = damage_reference_id(reference)
    assert rid in population_set.descriptive.members
    assert Covariate.ITEM_LEVEL in population_set.descriptive.declared_covariates
    assert "TARGET_ITEM_LEVEL_UNKNOWN" in population_set.descriptive.declared_limitations


def test_target_tier_pieces_unknown_makes_covariate_inadmissible_never_a_filter() -> None:
    target = _target(tier_pieces=None)
    reference = _make_log(character_name="AnyTier", tier_pieces=99)
    population_set = _populate(target, (reference,))
    rid = damage_reference_id(reference)
    assert rid in population_set.descriptive.members
    assert Covariate.TIER_PIECES in population_set.descriptive.declared_covariates
    assert "TARGET_TIER_PIECES_UNKNOWN" in population_set.descriptive.declared_limitations


# --- covariate isolation: DURATION (REQ, ladder widens) ----------------------


def test_duration_outside_strict_band_excludes_then_is_admitted_after_widening() -> None:
    target = _target(duration_s=300.0)
    # 20% off target: outside the 7% and 12% bands, exactly at the 20% band's
    # boundary (inclusive). tier_pieces/item_level match everywhere, so their
    # relaxation steps are attempted (fixed order, SPEC §6.3) but zero-delta.
    off_band = _make_log(character_name="Off", duration_s=360.0)
    padding = _refs(7)  # keeps the floor from being reached by padding alone
    population_set = _populate(target, (off_band, *padding))
    rid = damage_reference_id(off_band)
    assert rid in population_set.descriptive.members
    steps = population_set.descriptive.relaxation_steps
    assert [s.covariate for s in steps] == [
        Covariate.TIER_PIECES,
        Covariate.ITEM_LEVEL,
        Covariate.DURATION,
        Covariate.DURATION,
    ]
    assert steps[0].n_before == steps[0].n_after == 7
    assert steps[1].n_before == steps[1].n_after == 7
    assert steps[2].rule is RelaxationRule.WIDEN_BAND
    assert steps[2].band_pct == pytest.approx(DURATION_BANDS_PCT[1])
    assert steps[2].n_before == steps[2].n_after == 7
    last_step = steps[-1]
    assert last_step.rule is RelaxationRule.WIDEN_BAND
    assert last_step.band_pct == pytest.approx(DURATION_BANDS_PCT[2])
    assert rid in last_step.admitted_ids


def test_player_casts_per_minute_uses_the_positional_ladder_ceiling() -> None:
    target = _target(duration_s=300.0)
    # 20% off: inside the wide ladder's third level, but past the positional
    # ladder's last level (12%) — must never be admitted for this metric.
    off_band = _make_log(character_name="Off", duration_s=360.0)
    padding = _refs(7, external_buffs=frozenset())
    population_set = _populate(target, (off_band, *padding), metric="player_casts_per_minute")
    rid = damage_reference_id(off_band)
    assert rid not in population_set.descriptive.members
    assert population_set.descriptive.excluded_reasons[rid] == ("DURATION_BAND_MISMATCH",)
    assert population_set.descriptive.final_duration_band_pct == pytest.approx(0.12)


# --- covariate isolation matrix: every admitted covariate x every metric -----
#
# SPEC §12.1: "para cada covariável admitida e cada métrica, um conjunto em
# que apenas aquela covariável difere, verificando admissão/exclusão... e
# efeito sobre N". Each test below isolates exactly one covariate (the other
# three, plus identity/attempt-state/partition/damage-scope, are held equal
# to the target) and reads N/membership directly from the strict-level
# sensitivity row (independent review R3 completion) — this proves the
# isolation itself, without the ladder's own floor-seeking dynamics (already
# covered separately above) interfering with the reading.


@pytest.mark.parametrize("metric", _ALL_METRICS)
def test_external_buffs_isolation_at_strict_level_for_every_contracted_metric(metric: str) -> None:
    target = _target(external_buffs=frozenset({10060}))
    compatible = _make_log(character_name="Compatible", external_buffs=frozenset({10060}))
    mismatched = _make_log(character_name="Mismatched", external_buffs=frozenset())
    row = _strict_level_row(target, (compatible, mismatched), metric=metric)
    assert row.n == 1
    assert row.member_ids == (damage_reference_id(compatible),)


@pytest.mark.parametrize("metric", _ALL_METRICS)
def test_duration_isolation_at_strict_level_for_every_contracted_metric(metric: str) -> None:
    target = _target(duration_s=300.0)
    compatible = _make_log(character_name="Compatible", duration_s=300.0)
    # Outside even the widest ladder level (35%) of any metric.
    mismatched = _make_log(character_name="Mismatched", duration_s=1000.0)
    row = _strict_level_row(target, (compatible, mismatched), metric=metric)
    assert row.n == 1
    assert row.member_ids == (damage_reference_id(compatible),)


@pytest.mark.parametrize("metric", _AMPLITUDE_METRICS)
def test_item_level_isolation_at_strict_level_for_every_amplitude_metric(metric: str) -> None:
    target = _target(item_level=400.0)
    compatible = _make_log(character_name="Compatible", item_level=400.0)
    mismatched = _make_log(character_name="Mismatched", item_level=200.0)
    row = _strict_level_row(target, (compatible, mismatched), metric=metric)
    assert row.n == 1
    assert row.member_ids == (damage_reference_id(compatible),)


@pytest.mark.parametrize("metric", _ILVL_TIER_NOT_ADMITTED_METRICS)
def test_item_level_never_isolates_anyone_for_not_admitted_metrics(metric: str) -> None:
    target = _target(item_level=400.0)
    compatible = _make_log(character_name="Compatible", item_level=400.0)
    wildly_different = _make_log(character_name="Wild", item_level=1.0)
    row = _strict_level_row(target, (compatible, wildly_different), metric=metric)
    assert row.n == 2
    assert set(row.member_ids) == {
        damage_reference_id(compatible),
        damage_reference_id(wildly_different),
    }


@pytest.mark.parametrize("metric", _AMPLITUDE_METRICS)
def test_tier_pieces_isolation_at_strict_level_for_every_amplitude_metric(metric: str) -> None:
    target = _target(tier_pieces=4)
    compatible = _make_log(character_name="Compatible", tier_pieces=4)
    mismatched = _make_log(character_name="Mismatched", tier_pieces=0)
    row = _strict_level_row(target, (compatible, mismatched), metric=metric)
    assert row.n == 1
    assert row.member_ids == (damage_reference_id(compatible),)


@pytest.mark.parametrize("metric", _ILVL_TIER_NOT_ADMITTED_METRICS)
def test_tier_pieces_never_isolates_anyone_for_not_admitted_metrics(metric: str) -> None:
    target = _target(tier_pieces=4)
    compatible = _make_log(character_name="Compatible", tier_pieces=4)
    wildly_different = _make_log(character_name="Wild", tier_pieces=0)
    row = _strict_level_row(target, (compatible, wildly_different), metric=metric)
    assert row.n == 2
    assert set(row.member_ids) == {
        damage_reference_id(compatible),
        damage_reference_id(wildly_different),
    }


# --- exact membership per contracted metric (AC1) ----------------------------


@pytest.mark.parametrize(
    "metric",
    [
        "gross_ability_dps",
        "damage_per_event",
        "damage_events_per_second",
        "player_casts_per_minute",
        "aura_uptime_fraction",
        "gross_damage_share_pct",
    ],
)
def test_exact_membership_matches_hand_computed_expectation_per_metric(metric: str) -> None:
    target = _target()
    compatible = _refs(9)
    incompatible = _make_log(character_name="ItemLevelOff", item_level=1.0)
    population_set = _populate(target, (*compatible, incompatible), metric=metric)
    expected_members = tuple(sorted(damage_reference_id(r) for r in compatible))
    if metric in ("player_casts_per_minute", "aura_uptime_fraction"):
        # item_level is NOT_ADMITTED for these two: the "incompatible" log
        # is admitted too, since its only difference is item_level.
        expected_members = tuple(sorted({*expected_members, damage_reference_id(incompatible)}))
    assert population_set.descriptive.members == expected_members
    assert population_set.descriptive.n == len(expected_members)


# --- ladder behaviour (AC2) ---------------------------------------------------


def test_no_relaxation_step_when_strict_level_already_meets_the_floor() -> None:
    population_set = _populate(_target(), _refs(8))
    assert population_set.descriptive.relaxation_steps == ()
    assert population_set.descriptive.final_duration_band_pct == DURATION_BANDS_PCT[0]


def test_ladder_stops_after_tier_pieces_relaxation_alone() -> None:
    target = _target(tier_pieces=4)
    compatible = _refs(3, tier_pieces=4)
    tier_mismatched = _refs(5, tier_pieces=0, fight_id_start=100)
    population_set = _populate(target, (*compatible, *tier_mismatched))
    steps = population_set.descriptive.relaxation_steps
    assert len(steps) == 1
    assert steps[0].covariate is Covariate.TIER_PIECES
    assert steps[0].rule is RelaxationRule.DROP_FILTER
    assert steps[0].n_before == 3
    assert steps[0].n_after == 8
    assert population_set.descriptive.n == 8
    assert population_set.descriptive.relaxed_covariates == (Covariate.TIER_PIECES,)


def test_ladder_stops_after_tier_pieces_and_item_level_relaxation() -> None:
    target = _target(tier_pieces=4, item_level=400.0)
    compatible = _refs(2, tier_pieces=4, item_level=400.0, fight_id_start=1)
    only_item_ok = _refs(6, tier_pieces=0, item_level=400.0, fight_id_start=100)
    population_set = _populate(target, (*compatible, *only_item_ok))
    steps = population_set.descriptive.relaxation_steps
    assert [s.covariate for s in steps] == [Covariate.TIER_PIECES]
    assert population_set.descriptive.n == 8


def test_ladder_advances_to_duration_widening_only_after_covariates_exhausted() -> None:
    target = _target(tier_pieces=4, item_level=400.0, duration_s=300.0)
    compatible = _refs(2, tier_pieces=4, item_level=400.0, duration_s=300.0, fight_id_start=1)
    off_everything = _refs(6, tier_pieces=0, item_level=1.0, duration_s=360.0, fight_id_start=200)
    population_set = _populate(target, (*compatible, *off_everything))
    steps = population_set.descriptive.relaxation_steps
    covariates_in_order = [s.covariate for s in steps]
    assert covariates_in_order[:2] == [Covariate.TIER_PIECES, Covariate.ITEM_LEVEL]
    assert covariates_in_order[2] is Covariate.DURATION
    assert population_set.descriptive.n == 8
    # 20% off target duration: the 7% and 12% widen steps are zero-delta;
    # the floor is reached only once the band widens to 20%.
    assert population_set.descriptive.final_duration_band_pct == pytest.approx(
        DURATION_BANDS_PCT[2]
    )


def test_ladder_never_advances_past_the_floor_even_with_n_below_grading_threshold() -> None:
    # Exactly 8 eligible compatible references: the floor is met at the
    # strict level even though 8 < MIN_N_FOR_GRADING (15) — no step allowed.
    population_set = _populate(_target(), _refs(8))
    assert population_set.descriptive.n == 8
    assert population_set.descriptive.n < MIN_N_FOR_GRADING
    assert population_set.descriptive.relaxation_steps == ()
    assert population_set.descriptive.sufficiency is SufficiencyState.SUFFICIENT_FOR_COMPARISON


def test_ladder_exhausted_below_floor_reports_insufficient_without_fabricating_members() -> None:
    target = _target(duration_s=300.0)
    # Even at the widest (35%) band, still outside — never admitted.
    hopeless = _refs(3, duration_s=1000.0)
    population_set = _populate(target, hopeless)
    assert population_set.descriptive.n == 0
    assert population_set.descriptive.sufficiency is SufficiencyState.INSUFFICIENT
    assert population_set.descriptive.final_duration_band_pct == pytest.approx(
        (*DURATION_BANDS_PCT, 0.35)[-1]
    )
    last_step = population_set.descriptive.relaxation_steps[-1]
    assert last_step.n_before == 0
    assert last_step.n_after == 0
    assert last_step.admitted_ids == ()
    assert "INSUFFICIENT_FOR_COMPARISON" in population_set.descriptive.declared_limitations


def test_zero_delta_relaxation_step_is_recorded_not_omitted() -> None:
    # tier_pieces relaxation changes nothing (no reference is tier-mismatched);
    # only item_level relaxation actually admits anyone.
    target = _target(tier_pieces=4, item_level=400.0)
    compatible = _refs(2, tier_pieces=4, item_level=400.0, fight_id_start=1)
    only_tier_ok = _refs(6, tier_pieces=4, item_level=1.0, fight_id_start=200)
    population_set = _populate(target, (*compatible, *only_tier_ok))
    steps = population_set.descriptive.relaxation_steps
    assert steps[0].covariate is Covariate.TIER_PIECES
    assert steps[0].n_before == steps[0].n_after == 2
    assert steps[0].admitted_ids == ()
    assert steps[1].covariate is Covariate.ITEM_LEVEL
    assert steps[1].n_after == 8


# --- invariance to M2.1 scarcity (AC3) ---------------------------------------


def test_ineligible_reference_stays_excluded_at_every_relaxation_level() -> None:
    target = _target()
    ineligible = _make_log(character_name="WrongSpec", spec_name="Frost")
    padding = _refs(6, duration_s=1000.0)  # forces the ladder to fully exhaust
    population_set = _populate(target, (ineligible, *padding))
    rid = damage_reference_id(ineligible)
    assert rid not in population_set.descriptive.members
    assert population_set.descriptive.excluded_reasons[rid][0] == "BASIC_ELIGIBILITY_INELIGIBLE"
    assert "SPEC_MISMATCH" in population_set.descriptive.excluded_reasons[rid]


def test_indeterminate_reference_stays_excluded_at_every_relaxation_level() -> None:
    target = _target()
    indeterminate = _make_log(character_name="UnknownPartition", partition=None)
    padding = _refs(6, duration_s=1000.0)
    population_set = _populate(target, (indeterminate, *padding))
    rid = damage_reference_id(indeterminate)
    assert rid not in population_set.descriptive.members
    assert population_set.descriptive.excluded_reasons[rid][0] == "BASIC_ELIGIBILITY_INDETERMINATE"
    assert "PARTITION_UNKNOWN" in population_set.descriptive.excluded_reasons[rid]


def test_scarcity_never_promotes_an_ineligible_reference_to_reach_the_floor() -> None:
    target = _target()
    # Only 1 eligible reference in a pool of 9 — even fully relaxed, it can
    # never reach COHORT_MIN_HARD by promoting the ineligible ones.
    eligible = _make_log(character_name="OnlyOne", duration_s=1000.0)
    ineligible = tuple(
        _make_log(
            character_name=f"Bad{i}", class_name="Warrior", duration_s=1000.0, fight_id=i + 10
        )
        for i in range(8)
    )
    population_set = _populate(target, (eligible, *ineligible))
    assert population_set.descriptive.n <= 1
    assert population_set.descriptive.sufficiency is SufficiencyState.INSUFFICIENT
    for ref in ineligible:
        rid = damage_reference_id(ref)
        assert population_set.descriptive.excluded_reasons[rid][0] == "BASIC_ELIGIBILITY_INELIGIBLE"


# --- descriptive vs. aspirational separation (AC4) ---------------------------


def test_aspirational_is_a_subset_of_descriptive_of_the_same_metric() -> None:
    target = _target()
    references = _refs(12, dps=1000.0) + tuple(
        _make_log(character_name=f"High{i}", fight_id=100 + i, dps=5000.0 + i) for i in range(4)
    )
    population_set = _populate(target, references)
    descriptive_ids = set(population_set.descriptive.members)
    aspirational_ids = set(population_set.aspirational.members)
    assert aspirational_ids <= descriptive_ids
    assert population_set.aspirational.metric_id == population_set.descriptive.metric_id


def test_members_without_finite_dps_are_excluded_before_ordering_never_treated_as_zero() -> None:
    target = _target()
    no_dps = _make_log(character_name="NoDps", dps=None)
    nan_dps = _make_log(character_name="NanDps", dps=math.nan)
    normal = _refs(9, dps=500.0)
    population_set = _populate(target, (no_dps, nan_dps, *normal))
    assert damage_reference_id(no_dps) not in population_set.aspirational.members
    assert damage_reference_id(nan_dps) not in population_set.aspirational.members
    assert population_set.aspirational.excluded_reasons[damage_reference_id(no_dps)] == (
        "ASPIRATIONAL_ORDER_UNAVAILABLE",
    )
    assert population_set.aspirational.excluded_reasons[damage_reference_id(nan_dps)] == (
        "ASPIRATIONAL_ORDER_UNAVAILABLE",
    )


def test_fewer_than_reference_min_n_orderable_members_makes_aspirational_unavailable() -> None:
    target = _target()
    references = _refs(7, dps=500.0)  # 7 orderable descriptive members < 8
    population_set = _populate(target, references)
    assert population_set.descriptive.n == 7
    assert population_set.aspirational.sufficiency is SufficiencyState.UNAVAILABLE
    assert population_set.aspirational.members == ()
    assert population_set.aspirational.n == 0
    assert "ASPIRATIONAL_UNAVAILABLE" in population_set.aspirational.declared_limitations
    for rid in population_set.descriptive.members:
        assert population_set.aspirational.excluded_reasons[rid] == (
            "ASPIRATIONAL_ORDER_UNAVAILABLE",
        )


def test_available_aspirational_declares_outcome_selection_and_wcl_dps_ordering() -> None:
    target = _target()
    references = _refs(9, dps=500.0)
    population_set = _populate(target, references)
    assert population_set.aspirational.n > 0
    assert "ASPIRATIONAL_SELECTED_ON_OUTCOME" in population_set.aspirational.declared_limitations
    assert "ASPIRATIONAL_ORDERED_BY_WCL_DPS" in population_set.aspirational.declared_limitations


def test_orderable_member_excluded_from_upper_tail_gets_the_closed_reason() -> None:
    target = _target()
    low = _make_log(character_name="Low", dps=1.0)
    high = _refs(9, dps=5000.0)
    population_set = _populate(target, (low, *high))
    rid = damage_reference_id(low)
    if rid not in population_set.aspirational.members:
        assert population_set.aspirational.excluded_reasons[rid] == (
            "ASPIRATIONAL_NOT_IN_UPPER_TAIL",
        )


def test_aspirational_numbers_never_leak_into_descriptive_numbers() -> None:
    target = _target()
    references = _refs(12, dps=1000.0)
    population_set = _populate(target, references)
    assert population_set.descriptive.n == 12
    assert population_set.descriptive.n != population_set.aspirational.n
    assert population_set.descriptive.sufficiency != population_set.aspirational.sufficiency or (
        population_set.descriptive.members != population_set.aspirational.members
    )
    assert population_set.descriptive.kind is PopulationKind.DESCRIPTIVE
    assert population_set.aspirational.kind is PopulationKind.ASPIRATIONAL


# --- non-borrowing across metric_id (AC5) -------------------------------------


def test_n_and_sufficiency_belong_to_their_own_metric_id_not_borrowed() -> None:
    target = _target()
    references = _refs(9, uptime=None)  # uptime unobserved: aura metric unavailable
    dps_population = _populate(target, references, metric="gross_ability_dps")
    aura_population = _populate(target, references, metric="aura_uptime_fraction")
    assert dps_population.descriptive.n == 9
    assert aura_population.descriptive.n == 0
    assert aura_population.descriptive.sufficiency is SufficiencyState.INSUFFICIENT
    for rid in aura_population.descriptive.excluded_reasons:
        assert aura_population.descriptive.excluded_reasons[rid] == ("UPTIME_NOT_OBSERVED",)


def test_different_spell_ids_on_the_same_references_get_independent_populations() -> None:
    target = _target()
    references = _refs(9)
    eligibility = evaluate_references(target, references)
    populations = select_metric_populations(
        target,
        references,
        eligibility=eligibility,
        catalog=None,
        metric_ids=[("gross_ability_dps", 1), ("gross_ability_dps", 999)],
    )
    assert populations["gross_ability_dps:1"].descriptive.n == 9
    # spell_id 999 was never observed on any log: PLAYER_MECHANISM_NOT_OBSERVED.
    assert populations["gross_ability_dps:999"].descriptive.n == 0
    unavailable_reasons = populations["gross_ability_dps:999"].descriptive.excluded_reasons.values()
    assert all(reasons == ("PLAYER_MECHANISM_NOT_OBSERVED",) for reasons in unavailable_reasons)


# --- permutation invariance ----------------------------------------------------


def test_membership_and_steps_are_invariant_to_permutation_of_the_input_sequence() -> None:
    target = _target(tier_pieces=4)
    references = list(
        (
            *_refs(2, tier_pieces=4, fight_id_start=1),
            *_refs(4, tier_pieces=0, fight_id_start=100),
        )
    )
    baseline = _populate(target, tuple(references))
    for permutation in itertools.permutations(references):
        population_set = _populate(target, permutation)
        assert population_set.descriptive.members == baseline.descriptive.members
        assert population_set.descriptive.excluded_reasons == baseline.descriptive.excluded_reasons
        assert population_set.descriptive.relaxation_steps == baseline.descriptive.relaxation_steps
        assert population_set.aspirational.members == baseline.aspirational.members


def test_aspirational_ordering_is_invariant_to_permutation_including_dps_ties() -> None:
    target = _target()
    references = list(_refs(9, dps=500.0))  # all tied
    baseline = _populate(target, tuple(references))
    for permutation in itertools.permutations(references[:4]):
        population_set = _populate(target, (*permutation, *references[4:]))
        assert population_set.aspirational.members == baseline.aspirational.members


# --- determinism / canonical round-trip ----------------------------------------


def test_repeated_evaluation_and_canonical_dict_round_trip_are_stable() -> None:
    target = _target(tier_pieces=4)
    references = (
        *_refs(2, tier_pieces=4, fight_id_start=1),
        *_refs(6, tier_pieces=0, fight_id_start=100),
    )
    first = _populate(target, references)
    second = _populate(target, references)
    assert first == second
    canonical_first = json.dumps(dataclasses.asdict(first), sort_keys=True)
    canonical_second = json.dumps(dataclasses.asdict(second), sort_keys=True)
    assert canonical_first == canonical_second


# --- reference_id collision safety (independent review R1/R2 correction) -----
#
# M2.1 SPEC §4 explicitly preserves repeated reference_id entries and never
# establishes a uniqueness precondition; M2.2 does not deduplicate either
# (that is cohort_match's job, M2.3). A same-id log and its eligibility
# decision must therefore never be paired independently by first/last
# occurrence: that is exactly what let one physical log's covariate data
# get paired with a *different* physical log's eligibility decision when the
# two disagreed. When every log/decision sharing an id truly agrees, the
# collision is harmless and collapses deterministically; when they disagree,
# M2.2 refuses to guess which representation prevails and raises instead —
# it never silently produces an order-dependent result (violating AC3/§11).


def test_identity_collision_with_disagreeing_class_raises_instead_of_cross_associating() -> None:
    target = _target()
    incompatible = _make_log(character_name="Duplicate", class_name="Warrior")
    compatible = _make_log(character_name="Duplicate", class_name="Mage")
    for order in ((incompatible, compatible), (compatible, incompatible)):
        eligibility = evaluate_references(target, order)
        with pytest.raises(ValueError, match="collision with disagreeing data"):
            select_metric_population(
                target,
                order,
                metric="gross_ability_dps",
                spell_id=_SPELL_ID,
                eligibility=eligibility,
                catalog=None,
            )


def test_identity_collision_with_disagreeing_availability_raises_instead_of_changing_n() -> None:
    target = _target()
    missing_uptime = _make_log(character_name="Duplicate", uptime=None)
    observed_uptime = _make_log(character_name="Duplicate", uptime=0.5)
    for order in ((missing_uptime, observed_uptime), (observed_uptime, missing_uptime)):
        eligibility = evaluate_references(target, order)
        with pytest.raises(ValueError, match="collision with disagreeing data"):
            select_metric_population(
                target,
                order,
                metric="aura_uptime_fraction",
                spell_id=_SPELL_ID,
                eligibility=eligibility,
                catalog=None,
            )


def test_identity_collision_also_raises_from_covariate_sensitivity() -> None:
    # covariate_sensitivity shares _stage_a with select_metric_population and
    # must refuse the same disagreeing collision, not silently report a
    # misleading table.
    target = _target()
    incompatible = _make_log(character_name="Duplicate", class_name="Warrior")
    compatible = _make_log(character_name="Duplicate", class_name="Mage")
    references = (incompatible, compatible)
    eligibility = evaluate_references(target, references)
    with pytest.raises(ValueError, match="collision with disagreeing data"):
        covariate_sensitivity(
            target,
            references,
            metric="gross_ability_dps",
            spell_id=_SPELL_ID,
            eligibility=eligibility,
            catalog=None,
        )


def test_harmless_identical_duplicate_collapses_deterministically_without_raising() -> None:
    # Two dataclass-equal PlayerLog objects sharing an id (e.g. the same log
    # object counted twice upstream) is not an ambiguous collision: nothing
    # disagrees, so it collapses to a single member instead of raising.
    target = _target()
    first_copy = _make_log(character_name="Same")
    second_copy = _make_log(character_name="Same")
    assert first_copy == second_copy and first_copy is not second_copy
    padding = _refs(7)
    population_set = _populate(target, (first_copy, second_copy, *padding))
    assert damage_reference_id(first_copy) in population_set.descriptive.members
    assert population_set.descriptive.n == 8


def test_excluded_reasons_are_ordered_by_reference_id_regardless_of_input_order() -> None:
    target = _target(duration_s=300.0)
    buff_mismatch = _make_log(character_name="Z", external_buffs=frozenset({10060}))
    duration_mismatch = _make_log(character_name="A", duration_s=1000.0)
    forward = (buff_mismatch, duration_mismatch)
    reversed_order = (duration_mismatch, buff_mismatch)
    population_forward = _populate(target, forward)
    population_reversed = _populate(target, reversed_order)
    keys_forward = list(population_forward.descriptive.excluded_reasons)
    keys_reversed = list(population_reversed.descriptive.excluded_reasons)
    assert keys_forward == keys_reversed == sorted(keys_forward)
    # The output mapping's own iteration order is sorted — this must hold
    # without relying on json.dumps(..., sort_keys=True) to hide the defect.
    assert json.dumps(dataclasses.asdict(population_forward)) == json.dumps(
        dataclasses.asdict(population_reversed)
    )


def test_aspirational_excluded_reasons_are_also_ordered_by_reference_id() -> None:
    target = _target()
    # Mix of base (covariate) exclusions and aspirational-specific exclusions
    # (unorderable dps, not-in-upper-tail) so the merge must still be sorted.
    covariate_excluded = _make_log(character_name="Z", tier_pieces=0)
    no_dps = _make_log(character_name="M", dps=None)
    compatible_high = _refs(9, dps=5000.0, fight_id_start=10)
    low_dps = _make_log(character_name="B", dps=1.0)
    references = (covariate_excluded, no_dps, low_dps, *compatible_high)
    population_set = _populate(target, references)
    keys = list(population_set.aspirational.excluded_reasons)
    assert keys == sorted(keys)


# --- guard rails / ValueError contracts ----------------------------------------


def test_unknown_metric_raises_value_error_instead_of_guessing() -> None:
    target = _target()
    eligibility = evaluate_references(target, ())
    with pytest.raises(ValueError, match="unknown metric"):
        select_metric_population(
            target,
            (),
            metric="not_a_real_metric",
            spell_id=1,
            eligibility=eligibility,
            catalog=None,
        )


def test_eligibility_target_id_mismatch_raises_value_error() -> None:
    target = _target()
    other_target = _make_log(character_name="Other")
    eligibility = evaluate_references(other_target, ())
    with pytest.raises(ValueError, match="target"):
        select_metric_population(
            target,
            (),
            metric="gross_ability_dps",
            spell_id=1,
            eligibility=eligibility,
            catalog=None,
        )


def test_reference_missing_from_eligibility_population_raises_value_error() -> None:
    target = _target()
    known = _make_log(character_name="Known")
    eligibility = evaluate_references(target, (known,))
    stray = _make_log(character_name="Stray")
    with pytest.raises(ValueError, match="not present in eligibility population"):
        select_metric_population(
            target,
            (known, stray),
            metric="gross_ability_dps",
            spell_id=1,
            eligibility=eligibility,
            catalog=None,
        )


# --- B04 sensitivity artifact (read-only, never a selection input) -----------


def test_covariate_sensitivity_reports_every_ladder_level_without_stopping_at_the_floor() -> None:
    target = _target(tier_pieces=4, item_level=400.0, duration_s=300.0)
    compatible = _refs(2, tier_pieces=4, item_level=400.0, duration_s=300.0, fight_id_start=1)
    off_everything = _refs(6, tier_pieces=0, item_level=1.0, duration_s=360.0, fight_id_start=200)
    references = (*compatible, *off_everything)
    eligibility = evaluate_references(target, references)
    rows = covariate_sensitivity(
        target,
        references,
        metric="gross_ability_dps",
        spell_id=1,
        eligibility=eligibility,
        catalog=None,
    )
    # 1 strict + 2 covariate drops + 3 remaining wide-ladder widen steps = 6.
    assert len(rows) == 6
    assert rows[0].level_index == 0
    assert rows[0].relaxed_so_far == ()
    assert rows[0].n == 2
    # Membership is monotonically non-decreasing as covariates relax.
    for earlier, later in itertools.pairwise(rows):
        assert set(earlier.member_ids) <= set(later.member_ids)
    # The real selection stops at the floor (8, reached at level 4, where
    # duration widens to 20%); the sensitivity table keeps going regardless
    # and its last row is the widest level.
    assert rows[3].n == 2
    assert rows[4].n == 8
    assert rows[-1].n == 8
    assert rows[-1].duration_band_pct == pytest.approx((*DURATION_BANDS_PCT, 0.35)[-1])


def test_covariate_sensitivity_never_advances_metric_availability_gate_row_to_row() -> None:
    # Sensitivity varies covariates/duration only; metric-availability
    # exclusion is fixed across every row (Stage C is covariate-independent).
    target = _target()
    unavailable = _make_log(character_name="NoUptime", uptime=None)
    eligibility = evaluate_references(target, (unavailable,))
    rows = covariate_sensitivity(
        target,
        (unavailable,),
        metric="aura_uptime_fraction",
        spell_id=_SPELL_ID,
        eligibility=eligibility,
        catalog=None,
    )
    assert all(row.n == 0 for row in rows)


# --- read-only replay of real metadata ------------------------------------------

_ROLE_MAP: dict[str, str] = {"tanks": "tank", "healers": "healer", "dps": "dps"}


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
                _ROLE_MAP[role_key],  # type: ignore[arg-type]
                None,
                None,
                None,
            )
            logs.append(PlayerLog(fight, build, None, None, {}))
    return logs


def test_replay_gate1_scope_rankings_census_matches_manual_expectation() -> None:
    """Read-only replay over the only real WCL metadata available in this
    workspace (same 20-character fixture M2.1 replayed): no per-ability
    measurement was collected for these logs (damage_scope defaults to
    LEGACY_UNSCOPED, no measurement_provenance), so every metric_id lands at
    n=0 regardless of the one M2.1-eligible reference (Rohanlock) — this is
    the honest, calculated-by-hand result of this specific fixture, not a
    fabricated number. Expectation recorded in docs/m2-2-review-evidence.md.
    """
    before = snapshot_directory(_GATE1_FIXTURE_DIR)
    logs = _load_gate1_rankings_logs()
    target = next(log for log in logs if log.build.character_name == "Braska")
    references = tuple(log for log in logs if log.build.character_name != "Braska")
    eligibility = evaluate_references(target, references)

    population_set = select_metric_population(
        target,
        references,
        metric="gross_ability_dps",
        spell_id=1,
        eligibility=eligibility,
        catalog=None,
    )
    rohanlock = next(log for log in references if log.build.character_name == "Rohanlock")
    rohanlock_id = damage_reference_id(rohanlock)

    assert population_set.descriptive.n == 0
    assert population_set.descriptive.sufficiency is SufficiencyState.INSUFFICIENT
    assert population_set.descriptive.excluded_reasons[rohanlock_id] == (
        "LEGACY_UNSCOPED_SUBTOTAL",
    )
    ineligible_excluded = {
        rid: reasons
        for rid, reasons in population_set.descriptive.excluded_reasons.items()
        if rid != rohanlock_id
    }
    assert len(ineligible_excluded) == 18
    assert all(
        reasons[0] == "BASIC_ELIGIBILITY_INELIGIBLE" for reasons in ineligible_excluded.values()
    )
    assert population_set.aspirational.sufficiency is SufficiencyState.UNAVAILABLE

    after = snapshot_directory(_GATE1_FIXTURE_DIR)
    assert diff_snapshots(before, after) == []


def test_replay_gate1_scope_rankings_census_covers_all_six_contracted_metrics() -> None:
    """SPEC §12.10: the census must cover each contracted metric, not only
    `gross_ability_dps`. This fixture carries no per-ability measurement at
    all (see the docstring above), so every metric_id lands at n=0 — but the
    *reason* differs per metric, following each metric's own M1 observation
    path exactly (`observe` in metric_observations.py): the four accounting-
    based metrics fail accounting first (`LEGACY_UNSCOPED_SUBTOTAL`, since
    `damage_scope` defaults to LEGACY_UNSCOPED and no ability lookup is ever
    reached), while `player_casts_per_minute` and `aura_uptime_fraction` take
    independent code paths and fail on their own missing collection/uptime
    data. Table preserved in docs/m2-2-review-evidence.md.
    """
    before = snapshot_directory(_GATE1_FIXTURE_DIR)
    logs = _load_gate1_rankings_logs()
    target = next(log for log in logs if log.build.character_name == "Braska")
    references = tuple(log for log in logs if log.build.character_name != "Braska")
    eligibility = evaluate_references(target, references)
    rohanlock = next(log for log in references if log.build.character_name == "Rohanlock")
    rohanlock_id = damage_reference_id(rohanlock)

    expected_rohanlock_reason = {
        "gross_ability_dps": ("LEGACY_UNSCOPED_SUBTOTAL",),
        "damage_per_event": ("LEGACY_UNSCOPED_SUBTOTAL",),
        "damage_events_per_second": ("LEGACY_UNSCOPED_SUBTOTAL",),
        "gross_damage_share_pct": ("LEGACY_UNSCOPED_SUBTOTAL",),
        "player_casts_per_minute": ("CAST_COVERAGE_UNKNOWN",),
        "aura_uptime_fraction": ("UPTIME_NOT_OBSERVED",),
    }
    for metric, expected_reason in expected_rohanlock_reason.items():
        population_set = select_metric_population(
            target, references, metric=metric, spell_id=1, eligibility=eligibility, catalog=None
        )
        assert population_set.descriptive.n == 0
        assert population_set.descriptive.sufficiency is SufficiencyState.INSUFFICIENT
        assert population_set.descriptive.excluded_reasons[rohanlock_id] == expected_reason

    after = snapshot_directory(_GATE1_FIXTURE_DIR)
    assert diff_snapshots(before, after) == []


def test_covariate_sensitivity_over_real_gate1_scope_metadata() -> None:
    """SPEC §12.9: the B04 sensitivity artifact executed over real metadata,
    not only synthetic fixtures. The target's item_level/tier_pieces are
    unknown in this fixture (only ranking metadata was captured, no build
    snapshot), so both covariates are INADMISSIBLE_TARGET_UNKNOWN and never
    enter the ladder — only duration widens. Table preserved in
    docs/m2-2-review-evidence.md.
    """
    before = snapshot_directory(_GATE1_FIXTURE_DIR)
    logs = _load_gate1_rankings_logs()
    target = next(log for log in logs if log.build.character_name == "Braska")
    references = tuple(log for log in logs if log.build.character_name != "Braska")
    eligibility = evaluate_references(target, references)

    rows = covariate_sensitivity(
        target,
        references,
        metric="gross_ability_dps",
        spell_id=1,
        eligibility=eligibility,
        catalog=None,
    )
    assert [row.n for row in rows] == [0, 0, 0, 0]
    assert [row.duration_band_pct for row in rows] == [0.07, 0.12, 0.20, 0.35]
    assert all(row.member_ids == () for row in rows)
    assert rows[0].relaxed_so_far == ()
    assert all(row.relaxed_so_far == (Covariate.DURATION,) for row in rows[1:])

    after = snapshot_directory(_GATE1_FIXTURE_DIR)
    assert diff_snapshots(before, after) == []


def test_replay_over_data_raw_corpus_if_present() -> None:
    """SPEC §12.10: declare corpus absence explicitly rather than fabricate a
    number or silently skip without a message. This skip can never itself
    serve as passed acceptance proof for AC6 — the real M2.2 coverage proof
    in this suite is
    test_replay_gate1_scope_rankings_census_matches_manual_expectation.
    """
    paths = discover_corpus_paths()
    if not paths:
        pytest.skip(
            f"corpus real ausente em {CORPUS_ROOT}; a prova de cobertura real "
            "do M2.2 nesta suíte é "
            "test_replay_gate1_scope_rankings_census_matches_manual_expectation "
            "(replay de tests/fixtures/gate1_scope); ver docs/m2-2-review-evidence.md"
        )
    from botgitgud.ingest.parquet_codec import read_parquet_log

    logs = [read_parquet_log(p) for p in paths]
    target, *references = logs
    eligibility = evaluate_references(target, tuple(references))
    population_set = select_metric_population(
        target,
        tuple(references),
        metric="gross_ability_dps",
        spell_id=1,
        eligibility=eligibility,
        catalog=None,
    )
    assert population_set.policy_version == METRIC_POPULATION_POLICY_VERSION


# --- structural checks anchoring AC5 (imports) and AC6 (tooling config) --------


def test_metric_population_module_imports_are_limited_to_the_declared_allowlist() -> None:
    module_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "botgitgud"
        / "analysis"
        / "metric_population.py"
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
        "botgitgud.analysis.benchmark_reference.REFERENCE_MIN_N",
        "botgitgud.analysis.benchmark_reference.select_benchmark_reference",
        "botgitgud.analysis.cohort.COHORT_MIN_HARD",
        "botgitgud.analysis.cohort.POSITIONAL_BAND_PCT",
        "botgitgud.analysis.cohort.SANITY_BAND_PCT",
        "botgitgud.analysis.cohort_match.DURATION_BANDS_PCT",
        "botgitgud.analysis.cohort_match.DURATION_FLOOR_S",
        "botgitgud.analysis.cohort_match.ITEM_LEVEL_BAND",
        "botgitgud.analysis.cohort_match.TIER_PIECES_BAND",
        "botgitgud.analysis.grading.MIN_N_FOR_GRADING",
        "botgitgud.analysis.measurement.MetricObservation",
        "botgitgud.analysis.measurement.MetricStatus",
        "botgitgud.analysis.measurement.damage_reference_id",
        "botgitgud.analysis.metric_observations.UNITS",
        "botgitgud.analysis.metric_observations.observe",
        "botgitgud.analysis.reference_eligibility.EligibilityDecision",
        "botgitgud.analysis.reference_eligibility.ReferenceEligibility",
        "botgitgud.analysis.reference_eligibility.ReferenceEligibilityPopulation",
        "botgitgud.domain.external_buffs.EXTERNAL_OFFENSIVE_IDS",
        "botgitgud.domain.models.PlayerLog",
        "botgitgud.domain.spells.SpellCatalog",
    }
    assert imported_names == allowlist

    forbidden_prefixes = (
        "botgitgud.analysis.pipeline",
        "botgitgud.report",
        "botgitgud.phase4",
        "botgitgud.bot",
        "botgitgud.cli",
    )
    assert not any(name.startswith(forbidden_prefixes) for name in imported_names)


def test_pyproject_declares_the_ruff_and_pyright_configuration_used_by_documented_commands() -> (
    None
):
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
