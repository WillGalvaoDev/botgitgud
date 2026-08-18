from __future__ import annotations

from botgitgud.ingest.damage_aggregation import aggregate_damage_by_ability, parse_damage_events

PLAYER_ID = 6
PET_ID = 16
OTHER_PLAYER_ID = 7


def test_parse_damage_events_filters_by_source_and_sums_absorbed_into_amount() -> None:
    events = [
        {
            "type": "damage",
            "sourceID": PLAYER_ID,
            "targetID": 100,
            "abilityGameID": 999,
            "amount": 500,
            "absorbed": 50,
        },
        # a different, uninvolved source — must be dropped
        {
            "type": "damage",
            "sourceID": OTHER_PLAYER_ID,
            "targetID": 100,
            "abilityGameID": 999,
            "amount": 10_000,
        },
        # not a damage event — must be dropped
        {"type": "heal", "sourceID": PLAYER_ID, "targetID": 100, "abilityGameID": 999},
    ]
    parsed = parse_damage_events(events, frozenset({PLAYER_ID, PET_ID}))
    assert len(parsed) == 1
    assert parsed[0].amount == 550.0
    assert parsed[0].spell_id == 999
    assert parsed[0].target_id == 100


def test_parse_damage_events_includes_pet_sources() -> None:
    events = [
        {
            "type": "damage",
            "sourceID": PET_ID,
            "targetID": 100,
            "abilityGameID": 104318,
            "amount": 200,
        }
    ]
    parsed = parse_damage_events(events, frozenset({PLAYER_ID, PET_ID}))
    assert len(parsed) == 1
    assert parsed[0].source_id == PET_ID


def test_aggregate_reconciles_total_across_player_and_pets() -> None:
    """docs/schema_confirmado.md §5: entry.total already includes pet
    damage — aggregating player + pet events by ability must reproduce it
    exactly (live-verified 0.00% error for the Zarad fixture).
    """
    events = [
        {"type": "damage", "sourceID": PLAYER_ID, "targetID": 1, "abilityGameID": 1, "amount": 100},
        {"type": "damage", "sourceID": PLAYER_ID, "targetID": 2, "abilityGameID": 1, "amount": 150},
        {"type": "damage", "sourceID": PET_ID, "targetID": 1, "abilityGameID": 2, "amount": 300},
    ]
    known_total = 100 + 150 + 300
    parsed = parse_damage_events(events, frozenset({PLAYER_ID, PET_ID}))
    damage_by_ability, _ = aggregate_damage_by_ability(parsed, cast_counts={1: 2})
    aggregated_total = sum(ab.total for ab in damage_by_ability.values())
    assert aggregated_total == known_total


def test_aggregate_damage_by_ability_hits_and_casts() -> None:
    events = [
        {"type": "damage", "sourceID": PLAYER_ID, "targetID": 1, "abilityGameID": 1, "amount": 100},
        {"type": "damage", "sourceID": PLAYER_ID, "targetID": 2, "abilityGameID": 1, "amount": 100},
        {"type": "damage", "sourceID": PET_ID, "targetID": 1, "abilityGameID": 2, "amount": 50},
    ]
    parsed = parse_damage_events(events, frozenset({PLAYER_ID, PET_ID}))
    damage_by_ability, _avg_targets = aggregate_damage_by_ability(parsed, cast_counts={1: 2})

    ability1 = damage_by_ability[1]
    assert ability1.total == 200
    assert ability1.hits == 2
    assert ability1.casts == 2  # player's own cast_timeline count

    # spell 2 is pet-only: the player never personally cast it.
    ability2 = damage_by_ability[2]
    assert ability2.casts == 0


def test_avg_targets_per_cast_uses_distinct_targets_over_own_casts() -> None:
    events = [
        {"type": "damage", "sourceID": PLAYER_ID, "targetID": 1, "abilityGameID": 1, "amount": 10},
        {"type": "damage", "sourceID": PLAYER_ID, "targetID": 2, "abilityGameID": 1, "amount": 10},
        {"type": "damage", "sourceID": PLAYER_ID, "targetID": 1, "abilityGameID": 1, "amount": 10},
    ]
    parsed = parse_damage_events(events, frozenset({PLAYER_ID}))
    _damage_by_ability, avg_targets = aggregate_damage_by_ability(parsed, cast_counts={1: 2})
    # 2 distinct targets (1, 2) / 2 casts = 1.0
    assert avg_targets[1] == 1.0


def test_avg_targets_per_cast_omitted_for_zero_cast_abilities() -> None:
    events = [
        {"type": "damage", "sourceID": PET_ID, "targetID": 1, "abilityGameID": 2, "amount": 10}
    ]
    parsed = parse_damage_events(events, frozenset({PET_ID}))
    _damage_by_ability, avg_targets = aggregate_damage_by_ability(parsed, cast_counts={})
    assert 2 not in avg_targets
