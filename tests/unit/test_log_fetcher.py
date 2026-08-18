from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from botgitgud.domain.spells import SpellCatalog
from botgitgud.errors import FightNotFound, PlayerNotFound
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
    no_player: bool = False,
    no_fight: bool = False,
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
            }
        ]

    dps_group = (
        []
        if no_player
        else [
            {
                "id": player_id,
                "name": player_name,
                "type": class_name,
                "server": server,
                "region": region,
                "specs": [spec_name],
                "maxItemLevel": ilvl,
            }
        ]
    )
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
        else:
            pytest.fail(f"query GraphQL não reconhecida: {query[:80]}")

        self.calls.append(op)
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


def test_fetch_many_all_cache_hits_makes_zero_new_requests(tmp_path: Path) -> None:
    fetcher, transport, _store = _make_fetcher(tmp_path, _default_responses())
    fetcher.fetch("ABCDEFGHIJKLMNOP", 1, "Zarad")
    calls_before = len(transport.calls)

    refs = [LogRequest("ABCDEFGHIJKLMNOP", 1, "Zarad")] * 5
    results = fetcher.fetch_many(refs, max_workers=4)

    assert len(results) == 5
    assert all(r == results[0] for r in results)
    assert len(transport.calls) == calls_before  # no new API traffic at all
