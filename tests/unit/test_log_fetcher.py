from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from botgitgud.domain.models import FightRef, PlayerBuild, PlayerLog
from botgitgud.domain.spells import SpellCatalog
from botgitgud.errors import FightNotFound, PlayerNotFound, RateLimitBudgetExceeded
from botgitgud.ingest.log_fetcher import LogFetcher, LogRequest
from botgitgud.ingest.store import Store
from botgitgud.wcl.client import WclClient, WclClientConfig


def _token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})


def _rate_limit_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": {
                "rateLimitData": {
                    "limitPerHour": 3600,
                    "pointsSpentThisHour": 0,
                    "pointsResetIn": 3600,
                }
            }
        },
    )


def _meta_response(
    *,
    encounter_id: int = 3179,
    difficulty: int = 5,
    boss_name: str = "Fallen-King Salhadaar",
    start: int = 0,
    end: int = 100000,
    kill: bool = True,
    player_id: int = 6,
    player_name: str = "Zarad",
    class_name: str = "Warlock",
    spec_name: str = "Demonology",
    server: str = "Azralon",
    region: str = "US",
    damage_total: float = 1_000_000.0,
    ilvl: float = 283.0,
    abilities: list[dict[str, Any]] | None = None,
    combatant_info: dict[str, Any] | None = None,
    no_player: bool = False,
    no_fight: bool = False,
    phase_transitions: list[dict[str, Any]] | None = None,
    active_time_ms: float | None = None,
    death_events: list[dict[str, Any]] | None = None,
    actors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if no_fight:
        fights: list[Any] = []
    else:
        fights = [
            {
                "id": 1,
                "encounterID": encounter_id,
                "name": boss_name,
                "startTime": start,
                "endTime": end,
                "kill": kill,
                "difficulty": difficulty,
                "phaseTransitions": phase_transitions or [],
            }
        ]

    player_entry: dict[str, Any] = {
        "id": player_id,
        "name": player_name,
        "type": class_name,
        "server": server,
        "region": region,
        "specs": [spec_name],
        "maxItemLevel": ilvl,
    }
    if combatant_info is not None:
        player_entry["combatantInfo"] = combatant_info
    dps_group = [] if no_player else [player_entry]
    abilities = (
        abilities if abilities is not None else [{"guid": 104316, "name": "Call Dreadstalkers"}]
    )
    # T3.1: full uptime by default so pre-existing tests that don't care
    # about active_time_pct still get a deterministic, non-None value.
    active_time_ms = end - start if active_time_ms is None else active_time_ms

    return {
        "data": {
            "reportData": {
                "report": {
                    "fights": fights,
                    "masterData": {"actors": actors or []},
                    "table": {
                        "data": {
                            "playerDetails": {"dps": dps_group, "healers": [], "tanks": []},
                            "damageDone": [{"id": player_id, "total": damage_total}],
                            "deathEvents": death_events or [],
                        }
                    },
                    "castsTable": {
                        "data": {"entries": [{"id": player_id, "abilities": abilities}]}
                    },
                    "damageTable": {
                        "data": {"entries": [{"id": player_id, "activeTime": active_time_ms}]}
                    },
                }
            }
        }
    }


def _buffs_response(
    aura_guids: list[int] | None = None, *, total_time: float = 1000.0
) -> dict[str, Any]:
    auras = [
        {"guid": g, "name": f"Aura{g}", "totalUptime": 1000, "totalUses": 1}
        for g in (aura_guids or [])
    ]
    return {
        "data": {
            "reportData": {"report": {"table": {"data": {"auras": auras, "totalTime": total_time}}}}
        }
    }


def _events_response(
    events: list[dict[str, Any]], next_page: float | None = None
) -> dict[str, Any]:
    return {
        "data": {
            "reportData": {"report": {"events": {"data": events, "nextPageTimestamp": next_page}}}
        }
    }


def _report_rankings_response(
    *,
    partition: int | None = None,
    dps_characters: list[dict[str, Any]] | None = None,
    no_data: bool = False,
) -> dict[str, Any]:
    """T-DG.0/T-DG.1: shape of reportData.report.rankings — see
    ingest/fight_rankings.py's module docstring for field provenance.
    """
    if no_data:
        return {"data": {"reportData": {"report": {"rankings": {"data": []}}}}}
    return {
        "data": {
            "reportData": {
                "report": {
                    "rankings": {
                        "data": [
                            {
                                "fightID": 1,
                                "partition": partition,
                                "encounter": {"id": 3179, "name": "Fallen-King Salhadaar"},
                                "difficulty": 5,
                                "size": 20,
                                "kill": 1,
                                "duration": 100000,
                                "roles": {"dps": {"characters": dps_characters or []}},
                            }
                        ]
                    }
                }
            }
        }
    }


def _percentile_response(
    rank_percent: float | None, report_code: str, fight_id: int
) -> dict[str, Any]:
    if rank_percent is None:
        return {"data": {"characterData": {"character": None}}}
    return {
        "data": {
            "characterData": {
                "character": {
                    "encounterRankings": {
                        "ranks": [
                            {
                                "rankPercent": rank_percent,
                                "report": {"code": report_code, "fightID": fight_id},
                            }
                        ]
                    }
                }
            }
        }
    }


class _DispatchTransport(httpx.BaseTransport):
    """Routes by GraphQL operation name (substring of the query text) to a
    canned response; counts requests per operation so tests can assert
    "only fetched once" style cache behavior.
    """

    def __init__(self, responses: dict[str, list[dict[str, Any]] | dict[str, Any]]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if "oauth.battle.net" in str(request.url) or "warcraftlogs.com/oauth" in str(request.url):
            return _token_response()

        body = json.loads(request.content)
        query = body.get("query", "")
        if "rateLimitData" in query:
            return _rate_limit_response()
        if "GetPlayerMeta" in query:
            op = "meta"
        elif "GetPlayerDamageEvents" in query:
            op = "damage_events"
        elif "GetPlayerResourceEvents" in query:
            op = "resource_events"
        elif "GetPlayerEvents" in query:
            op = "events"
        elif "GetPercentile" in query:
            op = "percentile"
        elif "GetReportRankings" in query:
            op = "report_rankings"
        elif "GetPlayerDebuffs" in query:
            op = "debuffs"
        elif "GetPlayerBuffs" in query:
            op = "buffs"
        else:
            pytest.fail(f"query GraphQL não reconhecida: {query[:80]}")

        self.calls.append(op)
        # T2.1/T3.1: ops with a safe empty default don't need to be spelled
        # out by every test's response dict.
        _EMPTY_DEFAULTS = {
            "buffs": lambda: _buffs_response(),
            "debuffs": lambda: _buffs_response(),
            "damage_events": lambda: _events_response([]),
            "resource_events": lambda: _events_response([]),
            "report_rankings": lambda: _report_rankings_response(no_data=True),
        }
        if op in _EMPTY_DEFAULTS and op not in self._responses:
            return httpx.Response(200, json=_EMPTY_DEFAULTS[op]())
        payload = self._responses[op]
        item = payload.pop(0) if isinstance(payload, list) else payload
        return httpx.Response(200, json=item)


def _make_fetcher(
    tmp_path: Path, responses: dict[str, Any]
) -> tuple[LogFetcher, _DispatchTransport, Store]:
    transport = _DispatchTransport(responses)
    client = WclClient(WclClientConfig(client_id="id", client_secret="secret"), transport=transport)
    store = Store(tmp_path / "data")
    catalog = SpellCatalog(tmp_path / "spells.json", blizzard=None)
    fetcher = LogFetcher(client, store, catalog)
    return fetcher, transport, store


def _default_responses(report_code: str = "ABCDEFGHIJKLMNOP", fight_id: int = 1) -> dict[str, Any]:
    return {
        "meta": [_meta_response()],
        "events": [
            _events_response(
                [{"sourceID": 6, "type": "cast", "abilityGameID": 104316, "timestamp": 1300}]
            )
        ],
        "percentile": [_percentile_response(71.0, report_code, fight_id)],
    }


# -- documented acceptance criteria ------------------------------------------


def test_fetch_twice_hits_api_only_once(tmp_path: Path) -> None:
    fetcher, transport, _store = _make_fetcher(tmp_path, _default_responses())

    first = fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")
    meta_calls_after_first = transport.calls.count("meta")

    second = fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")
    meta_calls_after_second = transport.calls.count("meta")

    assert meta_calls_after_first == 1
    assert meta_calls_after_second == 1  # no new API call on the second fetch
    assert first == second


def test_force_bypasses_cache(tmp_path: Path) -> None:
    responses = {
        "meta": [_meta_response(), _meta_response(damage_total=2_000_000.0)],
        "events": [
            _events_response([]),
            _events_response([]),
        ],
        "percentile": [
            _percentile_response(None, "ABCDEFGHIJKLMNOP", 1),
            _percentile_response(None, "ABCDEFGHIJKLMNOP", 1),
        ],
    }
    fetcher, transport, _store = _make_fetcher(tmp_path, responses)

    fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")
    fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad", force=True)

    assert transport.calls.count("meta") == 2


# -- fetch() correctness -------------------------------------------------------


def test_fetch_builds_correct_player_log(tmp_path: Path) -> None:
    fetcher, _transport, _store = _make_fetcher(tmp_path, _default_responses())
    result = fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")

    assert result.fight.report_code == "ABCDEFGHIJKLMNOP"
    assert result.fight.encounter_id == 3179
    assert result.fight.duration_s == 100.0
    assert result.build.class_name == "Warlock"
    assert result.build.spec_name == "Demonology"
    assert result.build.role == "dps"
    assert result.build.item_level == 283.0
    assert result.dps == 10000.0  # 1_000_000 / 100s
    assert result.percentile == 71.0
    assert result.cast_timeline == {104316: (1.3,)}
    assert result.build.talent_hash is None  # no combatantInfo in this fixture
    assert result.build.tier_pieces is None
    assert result.build.has_augmentation is False
    assert result.build.external_buffs == frozenset()


def test_fetch_populates_augmentation_and_external_buffs(tmp_path: Path) -> None:
    responses = _default_responses()
    responses["buffs"] = [_buffs_response([395152, 10060, 999999])]  # Ebon Might + Power Infusion
    fetcher, _transport, _store = _make_fetcher(tmp_path, responses)

    result = fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")

    assert result.build.has_augmentation is True
    assert result.build.external_buffs == frozenset({10060})  # 999999 isn't a known external buff


def test_fetch_populates_talent_hash_and_tier_pieces_from_combatant_info(tmp_path: Path) -> None:
    responses = _default_responses()
    responses["meta"] = [
        _meta_response(
            combatant_info={
                "talentTree": [{"nodeID": 1, "rank": 1}],
                "gear": [{"setID": 1989}, {"setID": None}],
            }
        )
    ]
    fetcher, _transport, _store = _make_fetcher(tmp_path, responses)

    result = fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")

    assert result.build.talent_hash is not None
    assert result.build.tier_pieces == 1


# -- T3.1: performance features beyond casts -----------------------------------


def test_fetch_populates_active_time_pct_from_damage_table(tmp_path: Path) -> None:
    responses = _default_responses()
    responses["meta"] = [_meta_response(active_time_ms=90_000.0)]  # 90% of a 100_000ms fight
    fetcher, _transport, _store = _make_fetcher(tmp_path, responses)

    result = fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")

    assert result.active_time_pct == 0.9


def test_fetch_populates_deaths_and_downtime_from_summary_death_events(tmp_path: Path) -> None:
    responses = _default_responses()
    # deathTime is fight-relative ms (docs/schema_confirmado.md §7);
    # player's only cast in this fixture is at 1300ms == 1.3s.
    responses["meta"] = [_meta_response(death_events=[{"id": 6, "deathTime": 500.0}])]
    fetcher, _transport, _store = _make_fetcher(tmp_path, responses)

    result = fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")

    assert result.deaths == 1
    assert result.downtime_s == 1.3 - 0.5  # dies at 0.5s, next own cast at 1.3s


def test_fetch_ignores_other_players_deaths(tmp_path: Path) -> None:
    responses = _default_responses()
    responses["meta"] = [_meta_response(death_events=[{"id": 999, "deathTime": 500.0}])]
    fetcher, _transport, _store = _make_fetcher(tmp_path, responses)

    result = fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")

    assert result.deaths == 0
    assert result.downtime_s == 0.0


def test_fetch_populates_pet_aware_damage_by_ability_and_avg_targets(tmp_path: Path) -> None:
    responses = _default_responses()
    responses["meta"] = [_meta_response(actors=[{"id": 16, "petOwner": 6}])]
    responses["damage_events"] = [
        _events_response(
            [
                {
                    "type": "damage",
                    "sourceID": 6,
                    "targetID": 100,
                    "abilityGameID": 104316,
                    "amount": 500,
                },
                {
                    "type": "damage",
                    "sourceID": 16,  # pet of player 6
                    "targetID": 100,
                    "abilityGameID": 104318,
                    "amount": 200,
                },
                {
                    "type": "damage",
                    "sourceID": 7,  # unrelated player — must be excluded
                    "targetID": 100,
                    "abilityGameID": 999,
                    "amount": 10_000,
                },
            ]
        )
    ]
    fetcher, _transport, _store = _make_fetcher(tmp_path, responses)

    result = fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")

    assert result.damage_by_ability[104316].total == 500
    assert result.damage_by_ability[104316].casts == 1  # matches cast_timeline
    assert result.damage_by_ability[104318].total == 200
    assert result.damage_by_ability[104318].casts == 0  # player never cast the pet's ability
    assert 999 not in result.damage_by_ability
    assert result.avg_targets_per_cast[104316] == 1.0  # 1 distinct target / 1 cast


def test_fetch_populates_uptimes_from_buffs_and_debuffs(tmp_path: Path) -> None:
    responses = _default_responses()
    responses["buffs"] = [_buffs_response([395152], total_time=1000.0)]
    responses["debuffs"] = [_buffs_response([777], total_time=500.0)]
    fetcher, _transport, _store = _make_fetcher(tmp_path, responses)

    result = fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")

    assert result.uptimes[395152] == 1.0  # totalUptime=1000 / totalTime=1000
    assert result.uptimes[777] == 2.0  # totalUptime=1000 / totalTime=500 (fixture default)


def test_fetch_populates_resource_waste_for_the_player_only(tmp_path: Path) -> None:
    responses = _default_responses()
    responses["resource_events"] = [
        _events_response(
            [
                {"type": "resourcechange", "sourceID": 6, "resourceChangeType": 7, "waste": 5},
                {"type": "resourcechange", "sourceID": 999, "resourceChangeType": 7, "waste": 100},
            ]
        )
    ]
    fetcher, _transport, _store = _make_fetcher(tmp_path, responses)

    result = fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")

    assert result.resource_waste == {"Fragmentos de Alma": 5.0}


# -- T-DG.0: partition populated from report.rankings --------------------------


def test_fetch_populates_partition_from_report_rankings(tmp_path: Path) -> None:
    responses = _default_responses()
    responses["report_rankings"] = [_report_rankings_response(partition=3)]
    fetcher, _transport, _store = _make_fetcher(tmp_path, responses)

    result = fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")

    assert result.fight.partition == 3


def test_fetch_partition_is_none_when_report_rankings_unavailable(tmp_path: Path) -> None:
    """Never fabricated: no ranking data for this fight -> partition stays
    None, exactly as it did before T-DG.0 — it never becomes a guessed or
    default value.
    """
    fetcher, _transport, _store = _make_fetcher(tmp_path, _default_responses())

    result = fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")

    assert result.fight.partition is None


def test_fetch_persists_partition_to_store_and_parquet_path(tmp_path: Path) -> None:
    """docs/fase4-data-acquisition-plan.md T-DG.0 acceptance criterion: a
    new ingestion no longer writes logs.partition = NULL nor a
    partition=unknown parquet path when the API provides a real partition.
    """
    responses = _default_responses()
    responses["report_rankings"] = [_report_rankings_response(partition=3)]
    fetcher, _transport, store = _make_fetcher(tmp_path, responses)

    fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")

    df = store.query(
        "SELECT partition, parquet_path FROM logs WHERE report_code = $code",
        code="ABCDEFGHIJKLMNOP",
    )
    assert len(df) == 1
    partition, parquet_path = df["partition"][0], df["parquet_path"][0]
    assert partition == 3
    assert "partition=3" in parquet_path.replace("\\", "/")
    assert "partition=unknown" not in parquet_path


# -- T2.4: phase-aware fetching --------------------------------------------------


def test_fetch_derives_phase_intervals_and_phase_cast_timeline(tmp_path: Path) -> None:
    responses = _default_responses()
    responses["meta"] = [
        _meta_response(
            end=90_000,
            phase_transitions=[{"id": 1, "startTime": 0}, {"id": 2, "startTime": 45_000}],
        )
    ]
    responses["events"] = [
        _events_response(
            [
                {"sourceID": 6, "type": "cast", "abilityGameID": 104316, "timestamp": 10_000},
                {"sourceID": 6, "type": "cast", "abilityGameID": 104316, "timestamp": 60_000},
            ]
        )
    ]
    fetcher, _transport, _store = _make_fetcher(tmp_path, responses)

    result = fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")

    assert [iv.key for iv in result.fight.phase_intervals] == [(1, 0), (2, 0)]
    phase_times = result.phase_cast_timeline[104316]
    assert phase_times[(1, 0)] == (10.0,)
    assert phase_times[(2, 0)] == (15.0,)  # 60_000 - 45_000, relative to (2,0)'s own start


def test_fetch_falls_back_to_a_single_phase_when_the_fight_has_no_phase_data(
    tmp_path: Path,
) -> None:
    fetcher, _transport, _store = _make_fetcher(tmp_path, _default_responses())

    result = fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")

    assert len(result.fight.phase_intervals) == 1
    assert result.fight.phase_intervals[0].key == (0, 0)
    assert result.phase_cast_timeline[104316][(0, 0)] == result.cast_timeline[104316]


def test_fetch_persists_to_store(tmp_path: Path) -> None:
    fetcher, _transport, store = _make_fetcher(tmp_path, _default_responses())
    fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")

    assert store.has_log("ABCDEFGHIJKLMNOP", 1, "Zarad") is True


def test_fetch_learns_spell_names_into_catalog(tmp_path: Path) -> None:
    fetcher, _transport, _store = _make_fetcher(tmp_path, _default_responses())
    fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")

    info = fetcher._catalog.get(104316)
    assert info.name == "Call Dreadstalkers"
    assert info.source == "wcl"


def test_missing_fight_raises_fight_not_found(tmp_path: Path) -> None:
    responses = {
        "meta": [_meta_response(no_fight=True)],
        "events": [],
        "percentile": [],
    }
    fetcher, _transport, _store = _make_fetcher(tmp_path, responses)
    with pytest.raises(FightNotFound):
        fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")


def test_missing_player_raises_player_not_found(tmp_path: Path) -> None:
    responses = {
        "meta": [_meta_response(no_player=True)],
        "events": [],
        "percentile": [],
    }
    fetcher, _transport, _store = _make_fetcher(tmp_path, responses)
    with pytest.raises(PlayerNotFound):
        fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Nobody")


def test_percentile_none_when_character_not_found(tmp_path: Path) -> None:
    responses = {
        "meta": [_meta_response()],
        "events": [_events_response([])],
        "percentile": [_percentile_response(None, "ABCDEFGHIJKLMNOP", 1)],
    }
    fetcher, _transport, _store = _make_fetcher(tmp_path, responses)
    result = fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")
    assert result.percentile is None


# -- fetch_many -----------------------------------------------------------------


def test_fetch_many_reports_cache_hits_and_misses(tmp_path: Path) -> None:
    fetcher, _transport, _store = _make_fetcher(tmp_path, _default_responses())
    fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")  # pre-warm the cache for this one ref

    responses2 = {
        "meta": [_meta_response(player_name="Other", player_id=7)],
        "events": [_events_response([])],
        "percentile": [_percentile_response(None, "QRSTUVWXYZ123456", 2)],
    }
    fetcher._client = WclClient(
        WclClientConfig(client_id="id", client_secret="secret"),
        transport=_DispatchTransport({**responses2}),
    )
    # Reuse the same fetcher/store so the first ref is a cache hit and the
    # second is a genuine miss against the new transport.
    refs = [
        LogRequest("ABCDEFGHIJKLMNOP", 1, "Zarad"),
        LogRequest("QRSTUVWXYZ123456", 2, "Other"),
    ]
    results = fetcher.fetch_many(refs, max_workers=2)

    assert len(results) == 2
    assert results[0].build.character_name == "Zarad"
    assert results[1].build.character_name == "Other"


def test_fetch_many_tolerates_one_failed_ref_without_aborting_batch(tmp_path: Path) -> None:
    """T1.6: a reference-cohort fetch expects some fraction of ranked logs
    to 404 — one failing ref must not lose the rest of the batch.
    """
    responses = {
        "meta": [
            _meta_response(no_player=True),
            _meta_response(player_name="Other", player_id=7),
        ],
        "events": [_events_response([])],
        "percentile": [_percentile_response(None, "ABCDEFGHIJKLMNOP", 1)],
    }
    fetcher, _transport, _store = _make_fetcher(tmp_path, responses)

    refs = [
        LogRequest("ABCDEFGHIJKLMNOP", 1, "Nobody"),
        LogRequest("ABCDEFGHIJKLMNOP", 1, "Other"),
    ]
    # max_workers=1 makes the two submissions run strictly in order, so the
    # first "meta" response deterministically answers the first ref.
    results = fetcher.fetch_many(refs, max_workers=1)

    assert len(results) == 1
    assert results[0].build.character_name == "Other"


def _player_log_for(player_name: str, report_code: str) -> PlayerLog:
    fight = FightRef(
        report_code=report_code,
        fight_id=1,
        encounter_id=3179,
        boss_name="Fallen-King Salhadaar",
        difficulty=5,
        duration_s=100.0,
        kill=True,
    )
    build = PlayerBuild(
        character_name=player_name,
        server="Azralon",
        class_name="Warlock",
        spec_name="Demonology",
        role="dps",
        item_level=283.0,
        talent_hash=None,
        tier_pieces=None,
    )
    return PlayerLog(fight=fight, build=build, dps=10000.0, percentile=None, cast_timeline={})


def test_fetch_many_propagates_rate_limit_budget_exceeded_after_saving_partial_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T1.7: unlike a per-ref failure, RateLimitBudgetExceeded is a global
    condition — it must propagate out of fetch_many (so build-cohort can
    exit 75), but whatever succeeded before it fired must still be saved.
    """
    fetcher, _transport, store = _make_fetcher(tmp_path, _default_responses())

    def _fake_fetch(report_code: str, _fight_id: int, player: str) -> PlayerLog:
        if player == "Bad":
            raise RateLimitBudgetExceeded(
                "orçamento excedido", points_remaining=10.0, reset_in_seconds=60.0
            )
        return _player_log_for(player, report_code)

    monkeypatch.setattr(fetcher, "_fetch_from_api", _fake_fetch)

    refs = [
        LogRequest("CODE1", 1, "Good1"),
        LogRequest("CODE2", 1, "Bad"),
        LogRequest("CODE3", 1, "Good2"),
    ]
    with pytest.raises(RateLimitBudgetExceeded):
        fetcher.fetch_many(refs, max_workers=1)

    assert store.has_log("CODE1", 1, "Good1")
    assert store.has_log("CODE3", 1, "Good2")
    assert store.has_log("CODE2", 1, "Bad") is False


def test_fetch_many_all_cache_hits_makes_zero_new_requests(tmp_path: Path) -> None:
    fetcher, transport, _store = _make_fetcher(tmp_path, _default_responses())
    fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")
    calls_before = len(transport.calls)

    refs = [LogRequest("ABCDEFGHIJKLMNOP", 1, "Zarad")] * 5
    results = fetcher.fetch_many(refs, max_workers=4)

    assert len(results) == 5
    assert all(r == results[0] for r in results)
    assert len(transport.calls) == calls_before  # no new API traffic at all
