from __future__ import annotations

from botgitgud.domain.models import CollectionProvenance, CollectionStatus
from botgitgud.ingest.performance_parsing import (
    classify_resource_stream_provenance,
    compute_active_time_pct,
    compute_downtime_s,
    extract_pet_owner_map,
    find_damage_table_entry,
    parse_aura_table_provenance,
    parse_aura_uptimes,
    parse_death_events,
    parse_resource_type_counts,
    parse_resource_waste,
    parse_resource_waste_by_ability,
    pet_ids_for_owner,
)

# -- parse_aura_uptimes -----------------------------------------------------


def test_parse_aura_uptimes_computes_fraction_from_ms() -> None:
    data = {
        "totalTime": 1000.0,
        "auras": [{"guid": 123, "name": "Buff", "totalUptime": 500.0}],
    }
    result = parse_aura_uptimes(data)
    assert result == [(123, "Buff", 0.5)] or (
        result[0].spell_id == 123 and result[0].name == "Buff" and result[0].uptime_frac == 0.5
    )


def test_parse_aura_uptimes_empty_without_total_time() -> None:
    assert parse_aura_uptimes({"auras": [{"guid": 1, "totalUptime": 10}]}) == []


def test_parse_aura_uptimes_falls_back_to_spell_number_name() -> None:
    data = {"totalTime": 100.0, "auras": [{"guid": 5, "totalUptime": 10.0}]}
    result = parse_aura_uptimes(data)
    assert result[0].name == "Spell #5"


def test_parse_aura_uptimes_preserves_total_uses_and_band_order() -> None:
    data = {
        "totalTime": 1000,
        "auras": [
            {
                "guid": 5,
                "name": "Buff",
                "totalUptime": 300,
                "totalUses": 12,
                "bands": [
                    {"startTime": 200, "endTime": 300},
                    {"startTime": 10, "endTime": 20},
                ],
            }
        ],
    }
    aura = parse_aura_uptimes(data)[0]
    assert aura.total_uses == 12
    assert [(b.start_ms, b.end_ms) for b in aura.bands] == [(200, 300), (10, 20)]


# -- parse_death_events / compute_downtime_s ---------------------------------


def test_parse_death_events_filters_by_player_id() -> None:
    events = [
        {"id": 6, "deathTime": 1000.0},
        {"id": 7, "deathTime": 2000.0},
        {"id": 6, "deathTime": 3000.0},
    ]
    assert parse_death_events(events, 6) == [1000.0, 3000.0]


def test_compute_downtime_s_ends_at_next_own_cast() -> None:
    # died at 100s, next cast at 130s -> 30s downtime
    downtime = compute_downtime_s([100_000.0], [50.0, 130.0, 200.0], duration_s=345.0)
    assert downtime == 30.0


def test_compute_downtime_s_runs_to_fight_end_when_never_revived() -> None:
    # docs/schema_confirmado.md: Zarad's own fixture, dies and never casts again
    downtime = compute_downtime_s([178884.0], [50.0, 170.0], duration_s=345.146)
    assert downtime == 345.146 - 178.884


def test_compute_downtime_s_sums_across_multiple_deaths() -> None:
    downtime = compute_downtime_s(
        [10_000.0, 100_000.0], all_cast_times_s=[15.0, 110.0], duration_s=200.0
    )
    assert downtime == (15.0 - 10.0) + (110.0 - 100.0)


# -- active_time_pct ----------------------------------------------------------


def test_compute_active_time_pct_divides_by_duration_ms() -> None:
    entry = {"id": 6, "activeTime": 344303.0}
    pct = compute_active_time_pct(entry, duration_ms=345146.0)
    assert pct is not None
    assert abs(pct - 0.9976) < 0.001


def test_compute_active_time_pct_none_when_entry_missing() -> None:
    assert compute_active_time_pct(None, duration_ms=1000.0) is None


def test_find_damage_table_entry_matches_by_id() -> None:
    entries = [{"id": 5}, {"id": 6, "activeTime": 100.0}]
    entry = find_damage_table_entry(entries, 6)
    assert entry is not None
    assert entry["activeTime"] == 100.0
    assert find_damage_table_entry(entries, 999) is None


# -- pet owner map --------------------------------------------------------------


def test_extract_pet_owner_map_and_pet_ids_for_owner() -> None:
    actors = [
        {"id": 16, "petOwner": 6},
        {"id": 17, "petOwner": 13},
        {"id": 6, "petOwner": None},
    ]
    owner_map = extract_pet_owner_map(actors)
    assert owner_map == {16: 6, 17: 13}
    assert pet_ids_for_owner(owner_map, 6) == frozenset({16})


# -- resource waste ------------------------------------------------------------


def test_parse_resource_waste_sums_by_type_for_player_only() -> None:
    events = [
        {"type": "resourcechange", "sourceID": 6, "resourceChangeType": 7, "waste": 3},
        {"type": "resourcechange", "sourceID": 6, "resourceChangeType": 7, "waste": 2},
        {"type": "resourcechange", "sourceID": 6, "resourceChangeType": 0, "waste": 10},
        {"type": "resourcechange", "sourceID": 13, "resourceChangeType": 7, "waste": 100},
        {"type": "cast", "sourceID": 6, "resourceChangeType": 7, "waste": 999},
    ]
    waste = parse_resource_waste(events, 6)
    assert waste == {7: 5.0, 0: 10.0}


def test_parse_resource_waste_by_ability_reconciles_with_type_total() -> None:
    events = [
        {
            "type": "resourcechange",
            "sourceID": 6,
            "resourceChangeType": 7,
            "abilityGameID": 10,
            "waste": 3,
        },
        {
            "type": "resourcechange",
            "sourceID": 6,
            "resourceChangeType": 7,
            "abilityGameID": 11,
            "waste": 2,
        },
        {
            "type": "resourcechange",
            "sourceID": 13,
            "resourceChangeType": 7,
            "abilityGameID": 10,
            "waste": 100,
        },
    ]
    aggregate = parse_resource_waste(events, 6)
    detail = parse_resource_waste_by_ability(events, 6)
    assert detail == {7: {10: 3.0, 11: 2.0}}
    assert sum(detail[7].values()) == aggregate[7]


# -- M3.1: parse_resource_type_counts -----------------------------------------


def test_parse_resource_type_counts_pairs_count_with_waste_per_type() -> None:
    events = [
        {"type": "resourcechange", "sourceID": 6, "resourceChangeType": 7, "waste": 3},
        {"type": "resourcechange", "sourceID": 6, "resourceChangeType": 7, "waste": 0},
        {"type": "resourcechange", "sourceID": 6, "resourceChangeType": 0, "waste": 10},
        {"type": "resourcechange", "sourceID": 13, "resourceChangeType": 7, "waste": 100},
        {"type": "cast", "sourceID": 6, "resourceChangeType": 7, "waste": 999},
    ]
    counts, incomplete = parse_resource_type_counts(events, 6)
    assert counts == {7: (2, 3.0), 0: (1, 10.0)}
    assert incomplete == frozenset()


def test_parse_resource_type_counts_counts_zero_waste_events_as_observed() -> None:
    # A type seen with zero waste every time is APPLICABLE and OBSERVED
    # (D-M31-04) — its count must be nonzero even though its total is 0.0,
    # so a consumer can tell it apart from a type never seen at all.
    events = [{"type": "resourcechange", "sourceID": 6, "resourceChangeType": 5, "waste": 0}]
    counts, incomplete = parse_resource_type_counts(events, 6)
    assert counts == {5: (1, 0.0)}
    assert incomplete == frozenset()


def test_parse_resource_type_counts_empty_without_matching_events() -> None:
    counts, incomplete = parse_resource_type_counts([{"type": "cast", "sourceID": 6}], 6)
    assert counts == {}
    assert incomplete == frozenset()


def test_parse_resource_type_counts_never_fabricates_zero_for_a_missing_waste_field() -> None:
    # M3.1 independent review R2: docs/schema_confirmado.md §10 confirms a
    # real WCL resourcechange event always carries `waste` — an event
    # missing it entirely is not "zero waste observed", it is nothing
    # observed, and must not be counted as evidence for that type at all.
    # It DOES still mark the type incomplete (a lone event of that type,
    # unusable): a type wholly absent from the events isn't "incomplete",
    # it's simply never observed.
    events = [{"type": "resourcechange", "sourceID": 6, "resourceChangeType": 0, "timestamp": 1}]
    counts, incomplete = parse_resource_type_counts(events, 6)
    assert counts == {}
    assert incomplete == frozenset({0})


def test_parse_resource_type_counts_ignores_non_finite_or_non_numeric_waste() -> None:
    events = [
        {"type": "resourcechange", "sourceID": 6, "resourceChangeType": 0, "waste": float("nan")},
        {"type": "resourcechange", "sourceID": 6, "resourceChangeType": 0, "waste": "x"},
        {"type": "resourcechange", "sourceID": 6, "resourceChangeType": 0, "waste": True},
        # A genuine, valid zero-waste event for the SAME type is still counted...
        {"type": "resourcechange", "sourceID": 6, "resourceChangeType": 0, "waste": 0},
    ]
    counts, incomplete = parse_resource_type_counts(events, 6)
    # ...but the type is still flagged incomplete: three of its four events
    # could not be measured, so this subtotal is not the fight's true total.
    assert counts == {0: (1, 0.0)}
    assert incomplete == frozenset({0})


# -- M3.1 independent review R2 (residual): mixed valid/invalid evidence -------


def test_resource_type_counts_zero_plus_missing_waste_is_incomplete() -> None:
    events = [
        {"type": "resourcechange", "sourceID": 1, "resourceChangeType": 0, "waste": 0},
        {"type": "resourcechange", "sourceID": 1, "resourceChangeType": 0, "timestamp": 1},
    ]
    counts, incomplete = parse_resource_type_counts(events, 1)
    assert counts == {0: (1, 0.0)}
    assert incomplete == frozenset({0})


def test_resource_type_counts_zero_plus_nan_or_infinite_waste_is_incomplete() -> None:
    for bad in (float("nan"), float("inf"), float("-inf")):
        events = [
            {"type": "resourcechange", "sourceID": 1, "resourceChangeType": 0, "waste": 0},
            {"type": "resourcechange", "sourceID": 1, "resourceChangeType": 0, "waste": bad},
        ]
        counts, incomplete = parse_resource_type_counts(events, 1)
        assert counts == {0: (1, 0.0)}, bad
        assert incomplete == frozenset({0}), bad


def test_resource_type_counts_multiple_valid_zero_events_stay_complete() -> None:
    # Regression guard: several genuinely valid zero-waste events for the
    # same type must NOT be marked incomplete just because there are more
    # than one — completeness depends only on whether every event of that
    # type was usable, never on how many there were.
    events = [
        {"type": "resourcechange", "sourceID": 1, "resourceChangeType": 0, "waste": 0},
        {"type": "resourcechange", "sourceID": 1, "resourceChangeType": 0, "waste": 0},
        {"type": "resourcechange", "sourceID": 1, "resourceChangeType": 0, "waste": 0},
    ]
    counts, incomplete = parse_resource_type_counts(events, 1)
    assert counts == {0: (3, 0.0)}
    assert incomplete == frozenset()


def test_resource_type_counts_all_valid_positive_events_stay_complete() -> None:
    # Regression guard: genuinely complete, positive-waste evidence must
    # not be downgraded — R2's fix only degrades types with an actual
    # unusable event, never a type that simply has multiple valid events.
    events = [
        {"type": "resourcechange", "sourceID": 1, "resourceChangeType": 0, "waste": 5},
        {"type": "resourcechange", "sourceID": 1, "resourceChangeType": 0, "waste": 7},
    ]
    counts, incomplete = parse_resource_type_counts(events, 1)
    assert counts == {0: (2, 12.0)}
    assert incomplete == frozenset()


def test_resource_type_counts_incompleteness_is_isolated_per_type() -> None:
    # An unusable event for type 0 must not mark type 7 incomplete, and
    # type 7's genuinely complete evidence must stay usable.
    events = [
        {"type": "resourcechange", "sourceID": 1, "resourceChangeType": 0, "waste": 0},
        {"type": "resourcechange", "sourceID": 1, "resourceChangeType": 0, "timestamp": 1},
        {"type": "resourcechange", "sourceID": 1, "resourceChangeType": 7, "waste": 10},
    ]
    counts, incomplete = parse_resource_type_counts(events, 1)
    assert counts == {0: (1, 0.0), 7: (1, 10.0)}
    assert incomplete == frozenset({0})


# -- M3.1: parse_aura_table_provenance ----------------------------------------


def test_parse_aura_table_provenance_complete_when_total_time_matches_duration() -> None:
    data = {
        "totalTime": 345146.0,
        "auras": [{"guid": 5, "totalUptime": 300.0, "totalUses": 3}],
    }
    provenance = parse_aura_table_provenance(data, duration_ms=345146.0)
    assert provenance.collection == CollectionProvenance(
        CollectionStatus.COMPLETE, (), 0.0, 345146.0
    )
    assert provenance.total_time_ms == 345146.0
    assert provenance.auras == {5: (300.0, 3)}


def test_parse_aura_table_provenance_partial_on_total_time_mismatch() -> None:
    # This is the exact shape the M3.1 pre-implementation census cleared for
    # all 50 recorded aura tables (D-M31-03); a genuine mismatch must still
    # be caught, never silently trusted.
    data = {"totalTime": 1000.0, "auras": [{"guid": 5, "totalUptime": 300.0}]}
    provenance = parse_aura_table_provenance(data, duration_ms=345146.0)
    assert provenance.collection == CollectionProvenance(
        CollectionStatus.PARTIAL, ("AURA_TABLE_TOTAL_TIME_MISMATCH",), 0.0, 345146.0
    )
    assert provenance.total_time_ms == 1000.0
    assert provenance.auras == {5: (300.0, 0)}


def test_parse_aura_table_provenance_unavailable_when_shape_invalid() -> None:
    for bad in (None, {}, {"auras": []}, {"totalTime": 0.0, "auras": []}, {"totalTime": "x"}):
        provenance = parse_aura_table_provenance(bad, duration_ms=1000.0)
        assert provenance.collection == CollectionProvenance(
            CollectionStatus.UNKNOWN, ("STREAM_UNAVAILABLE",), 0.0, 1000.0
        )
        assert provenance.total_time_ms is None
        assert provenance.auras == {}


def test_parse_aura_table_provenance_skips_auras_without_total_uptime() -> None:
    data = {
        "totalTime": 1000.0,
        "auras": [{"guid": 1, "totalUptime": None}, {"guid": 2, "totalUptime": 400.0}],
    }
    provenance = parse_aura_table_provenance(data, duration_ms=1000.0)
    assert provenance.auras == {2: (400.0, 0)}


def test_parse_aura_table_provenance_records_the_requested_whole_fight_interval() -> None:
    # M3.1 independent review R3: QUERY_PLAYER_BUFFS/DEBUFFS take no
    # explicit startTime/endTime — WCL scopes them to the whole fight via
    # fightIDs — so the requested interval is always [0, duration_ms),
    # recorded on every branch, including outright failure.
    complete = parse_aura_table_provenance({"totalTime": 1000.0, "auras": []}, duration_ms=1000.0)
    assert complete.collection.requested_start_ms == 0.0
    assert complete.collection.requested_end_ms == 1000.0
    mismatch = parse_aura_table_provenance({"totalTime": 500.0, "auras": []}, duration_ms=1000.0)
    assert mismatch.collection.requested_start_ms == 0.0
    assert mismatch.collection.requested_end_ms == 1000.0
    unavailable = parse_aura_table_provenance(None, duration_ms=1000.0)
    assert unavailable.collection.requested_start_ms == 0.0
    assert unavailable.collection.requested_end_ms == 1000.0


def test_parse_aura_table_provenance_drops_nan_total_uptime_never_serializes_it() -> None:
    # M3.1 independent review R4: a non-finite totalUptime must never reach
    # the interface (D-M31-08 requires canonical JSON with no NaN) — it is
    # dropped exactly like a missing totalUptime, never fabricated as a
    # value nor left to poison the encoder downstream.
    data = {
        "totalTime": 1000.0,
        "auras": [
            {"guid": 1, "totalUptime": float("nan")},
            {"guid": 2, "totalUptime": float("inf")},
            {"guid": 3, "totalUptime": 400.0},
        ],
    }
    provenance = parse_aura_table_provenance(data, duration_ms=1000.0)
    assert provenance.auras == {3: (400.0, 0)}


def test_parse_aura_table_provenance_duplicate_guid_conflict_dropped_order_independent() -> None:
    # M3.1 independent review R5: two entries sharing a guid but disagreeing
    # on (totalUptime, totalUses) within the SAME table must not be
    # resolved by "last one wins" — the result must not depend on entry
    # order, matching the invariant already proven for the Buffs/Debuffs
    # cross-table conflict (D-M31-06).
    conflicting = [
        {"guid": 42, "totalUptime": 30000.0},
        {"guid": 42, "totalUptime": 60000.0},
    ]
    forward = parse_aura_table_provenance(
        {"totalTime": 300000.0, "auras": conflicting}, duration_ms=300000.0
    )
    backward = parse_aura_table_provenance(
        {"totalTime": 300000.0, "auras": conflicting[::-1]}, duration_ms=300000.0
    )
    assert forward.auras == backward.auras == {}


def test_parse_aura_table_provenance_duplicate_guid_agreeing_is_kept() -> None:
    # Repeating the SAME (totalUptime, totalUses) pair for a guid is not a
    # conflict — it collapses to that one value, not to zero exclusions.
    agreeing = [
        {"guid": 42, "totalUptime": 30000.0, "totalUses": 2},
        {"guid": 42, "totalUptime": 30000.0, "totalUses": 2},
    ]
    provenance = parse_aura_table_provenance(
        {"totalTime": 300000.0, "auras": agreeing}, duration_ms=300000.0
    )
    assert provenance.auras == {42: (30000.0, 2)}


# -- M3.1: classify_resource_stream_provenance --------------------------------


def test_classify_resource_stream_provenance_preserves_complete_unchanged() -> None:
    raw = CollectionProvenance(CollectionStatus.COMPLETE, (), 0.0, 1000.0)
    assert classify_resource_stream_provenance(raw) is raw


def test_classify_resource_stream_provenance_unknown_gets_canonical_reason_first() -> None:
    raw = CollectionProvenance(CollectionStatus.UNKNOWN, ("INVALID_INTERVAL",), 5.0, 3.0)
    result = classify_resource_stream_provenance(raw)
    assert result == CollectionProvenance(
        CollectionStatus.UNKNOWN, ("STREAM_UNAVAILABLE", "INVALID_INTERVAL"), 5.0, 3.0
    )


def test_classify_resource_stream_provenance_partial_gets_canonical_reason_first() -> None:
    raw = CollectionProvenance(CollectionStatus.PARTIAL, ("API_ERROR",), 0.0, 1000.0)
    result = classify_resource_stream_provenance(raw)
    assert result == CollectionProvenance(
        CollectionStatus.PARTIAL, ("STREAM_PARTIAL", "API_ERROR"), 0.0, 1000.0
    )
