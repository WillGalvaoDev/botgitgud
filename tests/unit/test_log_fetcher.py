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

    return {
        "data": {
            "reportData": {
                "report": {
                    "fights": fights,
                    "table": {
                        "data": {
                            "playerDetails": {"dps": dps_group, "healers": [], "tanks": []},
                            "damageDone": [{"id": player_id, "total": damage_total}],
                        }
                    },
                    "castsTable": {
                        "data": {"entries": [{"id": player_id, "abilities": abilities}]}
                    },
                }
            }
        }
    }


def _buffs_response(aura_guids: list[int] | None = None) -> dict[str, Any]:
    auras = [
        {"guid": g, "name": f"Aura{g}", "totalUptime": 1000, "totalUses": 1}
        for g in (aura_guids or [])
    ]
    return {"data": {"reportData": {"report": {"table": {"data": {"auras": auras}}}}}}


def _events_response(
    events: list[dict[str, Any]], next_page: float | None = None
) -> dict[str, Any]:
    return {
        "data": {
            "reportData": {"report": {"events": {"data": events, "nextPageTimestamp": next_page}}}
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
        elif "GetPlayerEvents" in query:
            op = "events"
        elif "GetPercentile" in query:
            op = "percentile"
        elif "GetPlayerBuffs" in query:
            op = "buffs"
        else:
            pytest.fail(f"query GraphQL não reconhecida: {query[:80]}")

        self.calls.append(op)
        if op == "buffs" and op not in self._responses:
            return httpx.Response(200, json=_buffs_response())  # T2.1: default empty buffs
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
