from __future__ import annotations

from pathlib import Path

from botgitgud.analysis.phases import derive_phase_intervals
from botgitgud.domain.spells import SpellCatalog
from botgitgud.ingest.wcl_parsing import (
    compute_talent_hash,
    count_tier_pieces,
    extract_damage_total,
    find_matching_rank_percent,
    find_player_in_details,
    learn_spells_from_casts_table,
    parse_aura_ids,
    parse_cast_events,
    parse_cast_events_by_phase,
)


def test_find_player_in_details_matches_case_insensitively() -> None:
    details = {"dps": [{"id": 6, "name": "Zarad", "type": "Warlock", "specs": ["Demonology"]}]}
    match = find_player_in_details(details, "zarad")
    assert match is not None
    assert match.player_id == 6
    assert match.class_name == "Warlock"
    assert match.spec_name == "Demonology"
    assert match.role == "dps"


def test_find_player_in_details_searches_every_role_group() -> None:
    details = {
        "dps": [],
        "healers": [],
        "tanks": [{"id": 9, "name": "Tanky", "type": "Warrior", "specs": ["Protection"]}],
    }
    match = find_player_in_details(details, "Tanky")
    assert match is not None
    assert match.role == "tank"


def test_find_player_in_details_returns_none_when_absent() -> None:
    assert find_player_in_details({"dps": []}, "Nobody") is None


def test_find_player_in_details_tolerates_playerdetails_as_a_list() -> None:
    """Observed against the real API (docs/desvios.md D-14): some reports'
    playerDetails comes back as [] instead of {dps: [], healers: [], ...}.
    """
    assert find_player_in_details([], "Zarad") is None  # type: ignore[arg-type]


def test_find_player_in_details_tolerates_a_role_group_as_a_non_list() -> None:
    assert find_player_in_details({"dps": {"unexpected": "shape"}}, "Zarad") is None


def test_find_player_in_details_skips_non_dict_entries_in_a_role_group() -> None:
    details = {"dps": ["not-a-player-dict", {"id": 6, "name": "Zarad", "type": "Warlock"}]}
    match = find_player_in_details(details, "Zarad")
    assert match is not None
    assert match.player_id == 6


def test_find_player_in_details_handles_dict_shaped_specs() -> None:
    details = {"dps": [{"id": 1, "name": "X", "type": "Mage", "specs": [{"spec": "Fire"}]}]}
    match = find_player_in_details(details, "X")
    assert match is not None
    assert match.spec_name == "Fire"


def test_find_player_in_details_parses_talent_hash_and_tier_pieces_from_combatant_info() -> None:
    details = {
        "dps": [
            {
                "id": 6,
                "name": "Zarad",
                "type": "Warlock",
                "specs": ["Demonology"],
                "combatantInfo": {
                    "talentTree": [{"id": 1, "rank": 1, "nodeID": 100}],
                    "gear": [{"slot": 0, "setID": 1989}, {"slot": 1, "setID": None}],
                },
            }
        ]
    }
    match = find_player_in_details(details, "Zarad")
    assert match is not None
    assert match.talent_hash is not None
    assert match.tier_pieces == 1


def test_find_player_in_details_leaves_talent_fields_none_without_combatant_info() -> None:
    details = {"dps": [{"id": 6, "name": "Zarad", "type": "Warlock", "specs": ["Demonology"]}]}
    match = find_player_in_details(details, "Zarad")
    assert match is not None
    assert match.talent_hash is None
    assert match.tier_pieces is None


# -- T2.1: talent hash / tier pieces / auras -------------------------------------


def test_compute_talent_hash_is_deterministic_regardless_of_input_order() -> None:
    tree_a = [{"nodeID": 100, "rank": 1}, {"nodeID": 50, "rank": 2}]
    tree_b = [{"nodeID": 50, "rank": 2}, {"nodeID": 100, "rank": 1}]
    assert compute_talent_hash(tree_a) == compute_talent_hash(tree_b)


def test_compute_talent_hash_differs_when_a_rank_changes() -> None:
    tree_a = [{"nodeID": 100, "rank": 1}]
    tree_b = [{"nodeID": 100, "rank": 2}]
    assert compute_talent_hash(tree_a) != compute_talent_hash(tree_b)


def test_compute_talent_hash_none_for_empty_tree() -> None:
    assert compute_talent_hash([]) is None


def test_compute_talent_hash_skips_malformed_entries() -> None:
    tree = [{"nodeID": 100, "rank": 1}, {"unexpected": "shape"}]
    assert compute_talent_hash(tree) == compute_talent_hash([{"nodeID": 100, "rank": 1}])


def test_count_tier_pieces_counts_only_items_with_a_set_id() -> None:
    gear = [{"setID": 1989}, {"setID": None}, {"setID": 1989}, {}]
    assert count_tier_pieces(gear) == 2


def test_count_tier_pieces_zero_when_no_tier_gear() -> None:
    gear = [{"setID": None}, {"setID": None}]
    assert count_tier_pieces(gear) == 0


def test_count_tier_pieces_none_when_gear_missing() -> None:
    assert count_tier_pieces(None) is None  # type: ignore[arg-type]


def test_parse_aura_ids_extracts_guids() -> None:
    buffs_data = {"auras": [{"guid": 395152, "name": "Ebon Might"}, {"guid": 10060}]}
    assert parse_aura_ids(buffs_data) == frozenset({395152, 10060})


def test_parse_aura_ids_empty_when_no_auras() -> None:
    assert parse_aura_ids({"auras": []}) == frozenset()
    assert parse_aura_ids({}) == frozenset()
    assert parse_aura_ids(None) == frozenset()  # type: ignore[arg-type]


def test_extract_damage_total_matches_by_id() -> None:
    damage_done = [{"id": 1, "total": 100.0}, {"id": 2, "total": 200.0}]
    assert extract_damage_total(damage_done, 2) == 200.0


def test_extract_damage_total_none_when_absent() -> None:
    assert extract_damage_total([{"id": 1, "total": 100.0}], 99) is None


def test_learn_spells_from_casts_table_only_learns_for_matching_player(tmp_path: Path) -> None:
    catalog = SpellCatalog(tmp_path / "spells.json", blizzard=None)
    entries = [
        {"id": 6, "abilities": [{"guid": 104316, "name": "Call Dreadstalkers"}]},
        {"id": 7, "abilities": [{"guid": 999, "name": "Other Player's Spell"}]},
    ]
    learn_spells_from_casts_table(entries, 6, catalog)
    assert catalog.get(104316).name == "Call Dreadstalkers"
    assert catalog.get(999).source == "unknown"  # never learned — belongs to a different actor


def test_parse_cast_events_filters_by_source_and_type() -> None:
    events = [
        {"sourceID": 6, "type": "cast", "abilityGameID": 104316, "timestamp": 1300},
        {"sourceID": 7, "type": "cast", "abilityGameID": 999, "timestamp": 1300},  # wrong source
        {"sourceID": 6, "type": "damage", "abilityGameID": 104316, "timestamp": 1300},  # wrong type
    ]
    timeline = parse_cast_events(events, player_id=6, start_time_ms=1000)
    assert timeline == {104316: [0.3]}


def test_parse_cast_events_skips_events_without_a_spell_id() -> None:
    events = [{"sourceID": 6, "type": "cast", "timestamp": 1000}]
    assert parse_cast_events(events, player_id=6, start_time_ms=1000) == {}


def test_find_matching_rank_percent_matches_report_and_fight() -> None:
    character = {
        "encounterRankings": {
            "ranks": [{"rankPercent": 71.0, "report": {"code": "ABC", "fightID": 1}}]
        }
    }
    assert find_matching_rank_percent(character, "ABC", 1) == 71.0
    assert find_matching_rank_percent(character, "ABC", 2) is None


def test_find_matching_rank_percent_none_when_character_missing() -> None:
    assert find_matching_rank_percent(None, "ABC", 1) is None


# -- T2.4: phase-aware cast parsing --------------------------------------------


def test_a_three_phase_fight_never_pairs_casts_across_phase_boundaries() -> None:
    """T2.4 acceptance: a fight with 3 phases -> independent groups per
    phase; a late cast from phase 1 lands in a different key than a
    phase 2 cast, never mixed together.
    """
    intervals = derive_phase_intervals(
        [
            {"id": 1, "startTime": 0},
            {"id": 2, "startTime": 100_000},
            {"id": 3, "startTime": 200_000},
        ],
        fight_start_ms=0,
        fight_end_ms=300_000,
    )
    events = [
        {"sourceID": 6, "type": "cast", "abilityGameID": 1, "timestamp": 90_000},  # phase 1
        {"sourceID": 6, "type": "cast", "abilityGameID": 1, "timestamp": 150_000},  # phase 2
        {"sourceID": 6, "type": "cast", "abilityGameID": 1, "timestamp": 250_000},  # phase 3
    ]
    timeline = parse_cast_events_by_phase(events, player_id=6, intervals=intervals)
    assert set(timeline[1].keys()) == {(1, 0), (2, 0), (3, 0)}
    assert timeline[1][(1, 0)] == [90.0]
    assert timeline[1][(2, 0)] == [50.0]  # 150_000 - 100_000, relative to (2,0)'s own start
    assert timeline[1][(3, 0)] == [50.0]  # 250_000 - 200_000


def test_zarad_fixture_third_occurrence_of_phase_1_never_aggregates_with_the_first() -> None:
    """T2.4 acceptance, with the real fixture's own 5 intervals: a cast in
    the 3rd occurrence of phase 1 lands under (1,2), never merged into
    (1,0)'s bucket.
    """
    transitions = [
        {"id": 1, "startTime": 1026037},
        {"id": 2, "startTime": 1128366},
        {"id": 1, "startTime": 1148363},
        {"id": 2, "startTime": 1249977},
        {"id": 1, "startTime": 1269978},
    ]
    intervals = derive_phase_intervals(transitions, fight_start_ms=1026037, fight_end_ms=1371183)
    events = [
        {"sourceID": 6, "type": "cast", "abilityGameID": 999, "timestamp": 1030000},  # (1,0)
        {"sourceID": 6, "type": "cast", "abilityGameID": 999, "timestamp": 1300000},  # (1,2)
    ]
    timeline = parse_cast_events_by_phase(events, player_id=6, intervals=intervals)
    assert set(timeline[999].keys()) == {(1, 0), (1, 2)}
    assert len(timeline[999][(1, 0)]) == 1
    assert len(timeline[999][(1, 2)]) == 1


def test_phase_less_fight_puts_every_cast_in_the_single_fallback_phase() -> None:
    """T2.4's mandatory fallback: no phase data -> identical grouping to
    the flat T0.7 behavior (one bucket for the whole fight)."""
    intervals = derive_phase_intervals([], fight_start_ms=0, fight_end_ms=1000)
    events = [
        {"sourceID": 6, "type": "cast", "abilityGameID": 1, "timestamp": 100},
        {"sourceID": 6, "type": "cast", "abilityGameID": 1, "timestamp": 500},
    ]
    timeline = parse_cast_events_by_phase(events, player_id=6, intervals=intervals)
    assert set(timeline[1].keys()) == {(0, 0)}
    assert timeline[1][(0, 0)] == [0.1, 0.5]


def test_parse_cast_events_by_phase_filters_by_source_and_type() -> None:
    intervals = derive_phase_intervals([], fight_start_ms=1000, fight_end_ms=2000)
    events = [
        {"sourceID": 6, "type": "cast", "abilityGameID": 104316, "timestamp": 1300},
        {"sourceID": 7, "type": "cast", "abilityGameID": 999, "timestamp": 1300},  # wrong source
        {"sourceID": 6, "type": "damage", "abilityGameID": 104316, "timestamp": 1300},  # wrong type
    ]
    timeline = parse_cast_events_by_phase(events, player_id=6, intervals=intervals)
    assert timeline == {104316: {(0, 0): [0.3]}}


def test_parse_cast_events_by_phase_skips_events_without_a_spell_id() -> None:
    intervals = derive_phase_intervals([], fight_start_ms=1000, fight_end_ms=2000)
    events = [{"sourceID": 6, "type": "cast", "timestamp": 1000}]
    assert parse_cast_events_by_phase(events, player_id=6, intervals=intervals) == {}


def test_parse_cast_events_by_phase_drops_a_timestamp_outside_every_interval() -> None:
    intervals = derive_phase_intervals([], fight_start_ms=1000, fight_end_ms=2000)
    events = [{"sourceID": 6, "type": "cast", "abilityGameID": 1, "timestamp": 500}]
    assert parse_cast_events_by_phase(events, player_id=6, intervals=intervals) == {}


def test_parse_cast_events_by_phase_skips_events_without_a_timestamp() -> None:
    intervals = derive_phase_intervals([], fight_start_ms=1000, fight_end_ms=2000)
    events = [{"sourceID": 6, "type": "cast", "abilityGameID": 1}]
    assert parse_cast_events_by_phase(events, player_id=6, intervals=intervals) == {}
