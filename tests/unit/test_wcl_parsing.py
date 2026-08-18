from __future__ import annotations

from pathlib import Path

from botgitgud.domain.spells import SpellCatalog
from botgitgud.ingest.wcl_parsing import (
    extract_damage_total,
    find_matching_rank_percent,
    find_player_in_details,
    learn_spells_from_casts_table,
    parse_cast_events,
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
