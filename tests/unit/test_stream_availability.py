"""M3.1 — stream-availability-v1 (docs/m3-1-specification.md): coverage of
the aura and resource streams, on top of PlayerLog.stream_provenance.

Matrix: zero/unknown/incomplete/not-applicable distinction (AC1), partial
never promoted to integral (AC2), cobertura e proveniência recuperáveis
(AC3), identidade por tipo/normalização sem soma entre tipos (AC4), um
stream não comprova outro (AC5), e o contraexemplo real do corpus
(percentile-only shape generalized to the aura-table-conflict shape).
"""

from __future__ import annotations

from botgitgud.analysis.measurement import MetricObservation, MetricStatus, damage_reference_id
from botgitgud.analysis.stream_availability import (
    STREAM_AVAILABILITY_POLICY_VERSION,
    ExternalBuffsAvailability,
    Stream,
    external_buffs_state,
    observe_aura_uptime,
    observe_legacy_resource_label,
    observe_resource_waste,
    stream_state,
)
from botgitgud.domain.models import (
    AuraTableProvenance,
    CollectionProvenance,
    CollectionStatus,
    FightRef,
    PlayerBuild,
    PlayerLog,
    ResourceStreamProvenance,
    StreamProvenance,
)

_DURATION_S = 300.0
_DURATION_MS = _DURATION_S * 1000.0

_FIGHT = FightRef(
    report_code="ABCDEFGHIJKLMNOP",
    fight_id=1,
    encounter_id=3179,
    boss_name="Fallen-King Salhadaar",
    difficulty=5,
    duration_s=_DURATION_S,
    kill=True,
)
_BUILD = PlayerBuild(
    character_name="Ref",
    server="Azralon",
    class_name="Warlock",
    spec_name="Demonology",
    role="dps",
    item_level=283.0,
    talent_hash=None,
    tier_pieces=4,
)

_COMPLETE = CollectionProvenance(CollectionStatus.COMPLETE)


def _log(
    *,
    uptimes: dict[int, float] | None = None,
    stream_provenance: StreamProvenance | None = None,
    duration_s: float = _DURATION_S,
) -> PlayerLog:
    fight = (
        _FIGHT
        if duration_s == _DURATION_S
        else FightRef(
            report_code=_FIGHT.report_code,
            fight_id=_FIGHT.fight_id,
            encounter_id=_FIGHT.encounter_id,
            boss_name=_FIGHT.boss_name,
            difficulty=_FIGHT.difficulty,
            duration_s=duration_s,
            kill=True,
        )
    )
    return PlayerLog(
        fight=fight,
        build=_BUILD,
        dps=None,
        percentile=None,
        cast_timeline={},
        uptimes=uptimes or {},
        stream_provenance=stream_provenance,
    )


def _aura_table(
    status: CollectionStatus = CollectionStatus.COMPLETE,
    reasons: tuple[str, ...] = (),
    auras: dict[int, tuple[float, int]] | None = None,
    total_time_ms: float | None = _DURATION_MS,
) -> AuraTableProvenance:
    return AuraTableProvenance(
        collection=CollectionProvenance(status, reasons),
        total_time_ms=total_time_ms,
        auras=auras or {},
    )


def _resources(
    status: CollectionStatus = CollectionStatus.COMPLETE,
    reasons: tuple[str, ...] = (),
    by_type: dict[int, tuple[int, float]] | None = None,
    incomplete_types: frozenset[int] = frozenset(),
) -> ResourceStreamProvenance:
    by_type = by_type or {}
    return ResourceStreamProvenance(
        collection=CollectionProvenance(status, reasons),
        player_event_count=sum(count for count, _waste in by_type.values()),
        by_type=by_type,
        incomplete_types=incomplete_types,
    )


def _observation_ok(obs: MetricObservation, value: float) -> None:
    assert obs.status is MetricStatus.AVAILABLE
    assert obs.value == value
    assert obs.reasons == ()


# -- policy version -------------------------------------------------------------


def test_policy_version_is_stream_availability_v1() -> None:
    assert STREAM_AVAILABILITY_POLICY_VERSION == "stream-availability-v1"


# -- stream_state: D-M31-07 legacy logs ------------------------------------------


def test_stream_state_is_coverage_unrecorded_for_a_legacy_log_without_provenance() -> None:
    log = _log(stream_provenance=None)
    for stream in Stream:
        state = stream_state(log, stream)
        assert state == CollectionProvenance(
            CollectionStatus.UNKNOWN, ("STREAM_COVERAGE_UNRECORDED",)
        )


def test_stream_state_is_coverage_unrecorded_when_one_table_was_never_built() -> None:
    # A StreamProvenance that only carries buffs (e.g. hand-built by a
    # future producer) still declares the other streams honestly.
    log = _log(stream_provenance=StreamProvenance(buffs=_aura_table()))
    assert stream_state(log, Stream.AURA_BUFFS).status is CollectionStatus.COMPLETE
    assert stream_state(log, Stream.AURA_DEBUFFS) == CollectionProvenance(
        CollectionStatus.UNKNOWN, ("STREAM_COVERAGE_UNRECORDED",)
    )
    assert stream_state(log, Stream.RESOURCES) == CollectionProvenance(
        CollectionStatus.UNKNOWN, ("STREAM_COVERAGE_UNRECORDED",)
    )


# -- observe_aura_uptime: AC1 zero/unknown/incomplete distinction ---------------


def test_aura_uptime_zero_observed_is_available_not_unknown() -> None:
    log = _log(
        stream_provenance=StreamProvenance(
            buffs=_aura_table(auras={5: (0.0, 0)}), debuffs=_aura_table()
        )
    )
    _observation_ok(observe_aura_uptime(log, 5), 0.0)


def test_aura_uptime_not_listed_in_two_complete_tables_is_unknown_not_zero() -> None:
    log = _log(stream_provenance=StreamProvenance(buffs=_aura_table(), debuffs=_aura_table()))
    obs = observe_aura_uptime(log, 999)
    assert obs.status is MetricStatus.UNKNOWN
    assert obs.value is None
    assert obs.reasons == ("AURA_NOT_LISTED",)


def test_aura_uptime_unavailable_stream_is_unknown_stream_unavailable() -> None:
    log = _log(
        stream_provenance=StreamProvenance(
            buffs=_aura_table(CollectionStatus.UNKNOWN, ("STREAM_UNAVAILABLE",)),
            debuffs=_aura_table(CollectionStatus.UNKNOWN, ("STREAM_UNAVAILABLE",)),
        )
    )
    obs = observe_aura_uptime(log, 5)
    assert obs.status is MetricStatus.UNKNOWN
    assert obs.value is None
    assert obs.reasons == ("STREAM_UNAVAILABLE",)


def test_aura_uptime_partial_total_time_mismatch_is_partial_not_available() -> None:
    log = _log(
        stream_provenance=StreamProvenance(
            buffs=_aura_table(
                CollectionStatus.PARTIAL, ("AURA_TABLE_TOTAL_TIME_MISMATCH",), total_time_ms=1000.0
            ),
            debuffs=_aura_table(
                CollectionStatus.PARTIAL, ("AURA_TABLE_TOTAL_TIME_MISMATCH",), total_time_ms=1000.0
            ),
        )
    )
    obs = observe_aura_uptime(log, 5)
    assert obs.status is MetricStatus.PARTIAL
    assert obs.value is None
    assert obs.reasons == ("AURA_TABLE_TOTAL_TIME_MISMATCH",)


def test_aura_uptime_invalid_duration_blocks_before_any_stream_read() -> None:
    log = _log(duration_s=0.0, stream_provenance=StreamProvenance())
    obs = observe_aura_uptime(log, 5)
    assert obs.status is MetricStatus.INVALID
    assert obs.reasons == ("INVALID_DURATION",)


# -- observe_aura_uptime: D-M31-06 conflict + D-M31-04 "herda o pior estado" ----


def test_aura_uptime_conflict_between_buffs_and_debuffs_is_invalid() -> None:
    # The corpus-real shape (M2.2's percentile-only duplicate), generalized
    # to the aura-table axis: two COMPLETE tables listing the SAME spell
    # with disagreeing uptimes must never be resolved by picking one.
    log = _log(
        stream_provenance=StreamProvenance(
            buffs=_aura_table(auras={5: (300.0, 1)}),
            debuffs=_aura_table(auras={5: (150.0, 1)}),
        )
    )
    obs = observe_aura_uptime(log, 5)
    assert obs.status is MetricStatus.INVALID
    assert obs.value is None
    assert obs.reasons == ("AURA_TABLE_CONFLICT",)


def test_aura_uptime_agreeing_tables_are_available_not_a_conflict() -> None:
    log = _log(
        stream_provenance=StreamProvenance(
            buffs=_aura_table(auras={5: (300.0, 1)}),
            debuffs=_aura_table(auras={5: (300.0, 1)}),
        )
    )
    _observation_ok(observe_aura_uptime(log, 5), 0.001)


def test_aura_uptime_listed_in_one_complete_table_only_is_available() -> None:
    log = _log(
        stream_provenance=StreamProvenance(
            buffs=_aura_table(auras={5: (150.0, 1)}), debuffs=_aura_table()
        )
    )
    _observation_ok(observe_aura_uptime(log, 5), 0.0005)


def test_aura_uptime_not_listed_in_one_complete_table_inherits_the_others_failure() -> None:
    # One table is COMPLETE and simply doesn't list this spell; the other
    # never answered. D-M31-04: still not AURA_NOT_LISTED (that requires
    # BOTH tables COMPLETE) — inherits the failed table's own state.
    log = _log(
        stream_provenance=StreamProvenance(
            buffs=_aura_table(),
            debuffs=_aura_table(CollectionStatus.UNKNOWN, ("STREAM_UNAVAILABLE",)),
        )
    )
    obs = observe_aura_uptime(log, 999)
    assert obs.status is MetricStatus.UNKNOWN
    assert obs.reasons == ("STREAM_UNAVAILABLE",)


def test_aura_uptime_not_listed_inherits_the_worse_of_two_non_complete_tables() -> None:
    # UNKNOWN (severity 2) beats PARTIAL (severity 1) regardless of which
    # table is "buffs" vs "debuffs".
    log = _log(
        stream_provenance=StreamProvenance(
            buffs=_aura_table(CollectionStatus.PARTIAL, ("AURA_TABLE_TOTAL_TIME_MISMATCH",)),
            debuffs=_aura_table(CollectionStatus.UNKNOWN, ("STREAM_UNAVAILABLE",)),
        )
    )
    obs = observe_aura_uptime(log, 5)
    assert obs.status is MetricStatus.UNKNOWN
    assert obs.reasons == ("STREAM_UNAVAILABLE",)

    # Swap the tables: the result must not depend on which one is "worse"
    # by position — only by status severity (determinism, D-M31 invariants).
    swapped = _log(
        stream_provenance=StreamProvenance(
            buffs=_aura_table(CollectionStatus.UNKNOWN, ("STREAM_UNAVAILABLE",)),
            debuffs=_aura_table(CollectionStatus.PARTIAL, ("AURA_TABLE_TOTAL_TIME_MISMATCH",)),
        )
    )
    obs_swapped = observe_aura_uptime(swapped, 5)
    assert obs_swapped.status is obs.status
    assert obs_swapped.reasons == obs.reasons


def test_aura_uptime_worse_merges_reasons_on_a_severity_tie() -> None:
    log = _log(
        stream_provenance=StreamProvenance(
            buffs=_aura_table(CollectionStatus.UNKNOWN, ("STREAM_UNAVAILABLE",)),
            debuffs=_aura_table(CollectionStatus.UNKNOWN, ("STREAM_COVERAGE_UNRECORDED",)),
        )
    )
    obs = observe_aura_uptime(log, 5)
    assert obs.status is MetricStatus.UNKNOWN
    assert obs.reasons == ("STREAM_COVERAGE_UNRECORDED", "STREAM_UNAVAILABLE")


# -- observe_aura_uptime: D-M31-07 legacy logs -----------------------------------


def test_aura_uptime_legacy_log_accepts_a_present_flat_uptime() -> None:
    log = _log(uptimes={5: 0.5}, stream_provenance=None)
    _observation_ok(observe_aura_uptime(log, 5), 0.5)


def test_aura_uptime_legacy_log_absent_aura_is_coverage_unrecorded() -> None:
    log = _log(uptimes={}, stream_provenance=None)
    obs = observe_aura_uptime(log, 5)
    assert obs.status is MetricStatus.UNKNOWN
    assert obs.reasons == ("STREAM_COVERAGE_UNRECORDED",)


def test_aura_uptime_legacy_log_out_of_range_value_is_invalid() -> None:
    # docs/m3-1-specification.md §2.2's own corpus fact: one real uptime is
    # -0.0071 (negative).
    log = _log(uptimes={5: -0.0071}, stream_provenance=None)
    obs = observe_aura_uptime(log, 5)
    assert obs.status is MetricStatus.INVALID
    assert obs.reasons == ("INVALID_UPTIME",)


def test_aura_uptime_source_identity_matches_damage_reference_id() -> None:
    log = _log(uptimes={5: 0.5}, stream_provenance=None)
    obs = observe_aura_uptime(log, 5)
    assert obs.source_identity == damage_reference_id(log)
    assert obs.metric_id == "aura_uptime_fraction:5"
    assert obs.denominator_kind == "FIGHT_DURATION_SECONDS"


# -- observe_resource_waste: AC1 zero/unknown/incomplete distinction ------------


def test_resource_waste_zero_observed_is_available_not_unknown() -> None:
    log = _log(stream_provenance=StreamProvenance(resources=_resources(by_type={7: (3, 0.0)})))
    _observation_ok(observe_resource_waste(log, 7), 0.0)


def test_resource_waste_type_never_observed_in_complete_collection_is_unknown() -> None:
    log = _log(stream_provenance=StreamProvenance(resources=_resources(by_type={})))
    obs = observe_resource_waste(log, 7)
    assert obs.status is MetricStatus.UNKNOWN
    assert obs.value is None
    assert obs.reasons == ("RESOURCE_TYPE_NOT_OBSERVED",)


# -- M3.1 independent review R2 (residual): mixed valid/invalid evidence --------
# within a COMPLETE stream must never present a partial subtotal as AVAILABLE.


def test_resource_waste_mixed_zero_and_missing_waste_is_partial_not_available() -> None:
    log = _log(
        stream_provenance=StreamProvenance(
            resources=_resources(by_type={0: (1, 0.0)}, incomplete_types=frozenset({0}))
        )
    )
    obs = observe_resource_waste(log, 0)
    assert obs.status is MetricStatus.PARTIAL
    assert obs.value == 0.0
    assert obs.reasons == ("STREAM_PARTIAL",)


def test_resource_waste_mixed_zero_and_non_finite_waste_is_partial_not_available() -> None:
    log = _log(
        stream_provenance=StreamProvenance(
            resources=_resources(by_type={0: (1, 0.0)}, incomplete_types=frozenset({0}))
        )
    )
    obs = observe_resource_waste(log, 0)
    assert obs.status is not MetricStatus.AVAILABLE
    assert obs.status is MetricStatus.PARTIAL


def test_resource_waste_all_events_unusable_is_partial_with_no_value() -> None:
    # Every event of this type was unusable: no subtotal to present at all,
    # but the type is not "never observed" either — it is incomplete.
    log = _log(
        stream_provenance=StreamProvenance(
            resources=_resources(by_type={}, incomplete_types=frozenset({0}))
        )
    )
    obs = observe_resource_waste(log, 0)
    assert obs.status is MetricStatus.PARTIAL
    assert obs.value is None
    assert obs.reasons == ("STREAM_PARTIAL",)


def test_resource_waste_multiple_valid_zero_events_still_available() -> None:
    # Regression guard against over-correction: genuinely complete evidence
    # (no incomplete_types entry) must stay AVAILABLE, however many events.
    log = _log(stream_provenance=StreamProvenance(resources=_resources(by_type={0: (3, 0.0)})))
    _observation_ok(observe_resource_waste(log, 0), 0.0)


def test_resource_waste_valid_positive_events_still_available() -> None:
    log = _log(stream_provenance=StreamProvenance(resources=_resources(by_type={0: (2, 12.0)})))
    _observation_ok(observe_resource_waste(log, 0), 60.0 * 12.0 / _DURATION_S)


def test_resource_waste_incompleteness_of_one_type_does_not_affect_another() -> None:
    log = _log(
        stream_provenance=StreamProvenance(
            resources=_resources(
                by_type={0: (1, 0.0), 7: (1, 10.0)}, incomplete_types=frozenset({0})
            )
        )
    )
    incomplete_obs = observe_resource_waste(log, 0)
    complete_obs = observe_resource_waste(log, 7)
    assert incomplete_obs.status is MetricStatus.PARTIAL
    _observation_ok(complete_obs, 60.0 * 10.0 / _DURATION_S)


def test_resource_waste_stream_unavailable_is_unknown_stream_unavailable() -> None:
    log = _log(
        stream_provenance=StreamProvenance(
            resources=_resources(
                CollectionStatus.UNKNOWN, ("STREAM_UNAVAILABLE", "INVALID_INTERVAL")
            )
        )
    )
    obs = observe_resource_waste(log, 7)
    assert obs.status is MetricStatus.UNKNOWN
    assert obs.value is None
    assert obs.reasons == ("STREAM_UNAVAILABLE", "INVALID_INTERVAL")


def test_resource_waste_partial_collection_with_type_present_keeps_a_marked_value() -> None:
    log = _log(
        stream_provenance=StreamProvenance(
            resources=_resources(
                CollectionStatus.PARTIAL, ("STREAM_PARTIAL", "API_ERROR"), by_type={7: (2, 60.0)}
            )
        )
    )
    obs = observe_resource_waste(log, 7)
    assert obs.status is MetricStatus.PARTIAL
    assert obs.value == 60.0 * 60.0 / _DURATION_S  # 60 units/min conversion
    assert obs.reasons == ("STREAM_PARTIAL", "API_ERROR")


def test_resource_waste_partial_collection_without_the_type_has_no_value() -> None:
    # A page failed before this type could ever appear — never fabricate a
    # zero subtotal for a type the partial collection never reached.
    log = _log(
        stream_provenance=StreamProvenance(
            resources=_resources(CollectionStatus.PARTIAL, ("STREAM_PARTIAL", "API_ERROR"))
        )
    )
    obs = observe_resource_waste(log, 7)
    assert obs.status is MetricStatus.PARTIAL
    assert obs.value is None
    assert obs.reasons == ("STREAM_PARTIAL", "API_ERROR")


def test_resource_waste_invalid_duration_blocks_before_any_stream_read() -> None:
    log = _log(duration_s=0.0, stream_provenance=StreamProvenance())
    obs = observe_resource_waste(log, 7)
    assert obs.status is MetricStatus.INVALID
    assert obs.reasons == ("INVALID_DURATION",)


def test_resource_waste_legacy_log_is_always_coverage_unrecorded() -> None:
    # Unlike aura uptime, D-M31-07 accepts NO legacy resource-waste value —
    # the historical paginator recorded no coverage proof for this stream.
    log = _log(stream_provenance=None)
    obs = observe_resource_waste(log, 7)
    assert obs.status is MetricStatus.UNKNOWN
    assert obs.value is None
    assert obs.reasons == ("STREAM_COVERAGE_UNRECORDED",)


def test_resource_waste_unit_and_denominator_are_per_minute() -> None:
    log = _log(stream_provenance=StreamProvenance(resources=_resources(by_type={7: (1, 30.0)})))
    obs = observe_resource_waste(log, 7)
    assert obs.unit == "7 units/min"
    assert obs.denominator_kind == "FIGHT_DURATION_MINUTES"
    assert obs.metric_id == "resource_waste_per_minute:7"


# -- AC4: identity by integer type, no cross-type aggregation -------------------


def test_resource_waste_two_types_on_the_same_log_are_independent_observations() -> None:
    # docs/m3-1-specification.md §2.2: 280 corpus logs have more than one
    # resource type (e.g. Unholy's Energia + Poder Rúnico) — each must be
    # its own observation, never summed.
    log = _log(
        stream_provenance=StreamProvenance(
            resources=_resources(by_type={0: (5, 100.0), 7: (3, 30.0)})
        )
    )
    energy = observe_resource_waste(log, 0)
    runic = observe_resource_waste(log, 7)
    assert energy.metric_id != runic.metric_id
    assert energy.value != runic.value
    assert energy.value == 60.0 * 100.0 / _DURATION_S
    assert runic.value == 60.0 * 30.0 / _DURATION_S


def test_stream_availability_module_never_sums_across_resource_types() -> None:
    import ast
    from pathlib import Path

    module_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "botgitgud"
        / "analysis"
        / "stream_availability.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    forbidden = {"sum", "fsum"}
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not (called & forbidden)


# -- AC5: one stream never proves another ---------------------------------------


def test_resources_failing_does_not_affect_aura_uptime() -> None:
    log = _log(
        stream_provenance=StreamProvenance(
            buffs=_aura_table(auras={5: (150.0, 1)}),
            debuffs=_aura_table(),
            resources=_resources(CollectionStatus.UNKNOWN, ("STREAM_UNAVAILABLE",)),
        )
    )
    _observation_ok(observe_aura_uptime(log, 5), 0.0005)


def test_auras_failing_does_not_affect_resource_waste() -> None:
    log = _log(
        stream_provenance=StreamProvenance(
            buffs=_aura_table(CollectionStatus.UNKNOWN, ("STREAM_UNAVAILABLE",)),
            debuffs=_aura_table(CollectionStatus.UNKNOWN, ("STREAM_UNAVAILABLE",)),
            resources=_resources(by_type={7: (1, 30.0)}),
        )
    )
    _observation_ok(observe_resource_waste(log, 7), 60.0 * 30.0 / _DURATION_S)


def test_external_buffs_state_depends_only_on_buffs_table() -> None:
    log = _log(
        stream_provenance=StreamProvenance(
            buffs=_aura_table(),
            debuffs=_aura_table(CollectionStatus.UNKNOWN, ("STREAM_UNAVAILABLE",)),
            resources=_resources(CollectionStatus.UNKNOWN, ("STREAM_UNAVAILABLE",)),
        )
    )
    assert external_buffs_state(log) == ExternalBuffsAvailability(MetricStatus.AVAILABLE)


def test_external_buffs_state_reflects_buffs_table_failure() -> None:
    log = _log(
        stream_provenance=StreamProvenance(
            buffs=_aura_table(CollectionStatus.UNKNOWN, ("STREAM_UNAVAILABLE",))
        )
    )
    assert external_buffs_state(log) == ExternalBuffsAvailability(
        MetricStatus.UNKNOWN, ("STREAM_UNAVAILABLE",)
    )


def test_external_buffs_state_legacy_log_is_coverage_unrecorded() -> None:
    log = _log(stream_provenance=None)
    assert external_buffs_state(log) == ExternalBuffsAvailability(
        MetricStatus.UNKNOWN, ("STREAM_COVERAGE_UNRECORDED",)
    )


# -- observe_legacy_resource_label: AC4's reachable identity->availability caller


def test_legacy_resource_label_known_label_defers_to_coverage_unrecorded() -> None:
    # A resolvable label never becomes AVAILABLE nor changes D-M31-07's
    # gate — this delegates exactly to observe_resource_waste(log, 0).
    log = _log(stream_provenance=None)
    obs = observe_legacy_resource_label(log, "Mana")
    assert obs == observe_resource_waste(log, 0)
    assert obs.status is MetricStatus.UNKNOWN
    assert obs.reasons == ("STREAM_COVERAGE_UNRECORDED",)


def test_legacy_resource_label_numbered_fallback_defers_to_coverage_unrecorded() -> None:
    log = _log(stream_provenance=None)
    obs = observe_legacy_resource_label(log, "recurso #16")
    assert obs == observe_resource_waste(log, 16)
    assert obs.status is MetricStatus.UNKNOWN


def test_legacy_resource_label_unmappable_is_invalid_regardless_of_coverage() -> None:
    # Identity failure, not a coverage question: INVALID even though the
    # log has no stream_provenance at all.
    log = _log(stream_provenance=None)
    obs = observe_legacy_resource_label(log, "unmappable")
    assert obs.status is MetricStatus.INVALID
    assert obs.value is None
    assert obs.reasons == ("LEGACY_RESOURCE_LABEL_UNMAPPABLE",)


def test_legacy_resource_label_unmappable_is_invalid_even_with_full_coverage() -> None:
    # An unmappable label is unmappable regardless of the log's own stream
    # coverage — identity failure is independent of D-M31-07's gate.
    log = _log(stream_provenance=StreamProvenance(resources=_resources(by_type={0: (1, 5.0)})))
    obs = observe_legacy_resource_label(log, "unmappable")
    assert obs.status is MetricStatus.INVALID
    assert obs.reasons == ("LEGACY_RESOURCE_LABEL_UNMAPPABLE",)


def test_legacy_resource_label_resolvable_never_becomes_available_from_a_legacy_log() -> None:
    # Explicit guard against "transformar o histórico em AVAILABLE"
    # (re-review.md): every resolvable label on a log with no
    # stream_provenance stays UNKNOWN, never AVAILABLE.
    for label in ("Mana", "Fúria (Rage)", "recurso #999"):
        log = _log(stream_provenance=None)
        obs = observe_legacy_resource_label(log, label)
        assert obs.status is not MetricStatus.AVAILABLE
