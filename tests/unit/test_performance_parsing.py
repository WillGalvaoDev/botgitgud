from __future__ import annotations

from botgitgud.ingest.performance_parsing import (
    compute_active_time_pct,
    compute_downtime_s,
    extract_pet_owner_map,
    find_damage_table_entry,
    parse_aura_uptimes,
    parse_death_events,
    parse_resource_waste,
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
