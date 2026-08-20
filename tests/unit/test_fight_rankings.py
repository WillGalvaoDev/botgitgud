from __future__ import annotations

from typing import Any

import pytest

from botgitgud.errors import TransientApiError
from botgitgud.ingest.fight_rankings import (
    fetch_fight_rankings,
    parse_report_rankings,
    parse_report_rankings_all,
)


def _real_shaped_response() -> dict[str, Any]:
    """Mirrors the live shape measured for docs/schema_confirmado.md §13.3
    (fight 21 of report 2CbQfkJ47Nxq6cDT, encounter 3179, partition 3).
    """
    return {
        "data": {
            "reportData": {
                "report": {
                    "rankings": {
                        "data": [
                            {
                                "fightID": 21,
                                "partition": 3,
                                "encounter": {"id": 3179, "name": "Fallen-King Salhadaar"},
                                "difficulty": 5,
                                "size": 20,
                                "kill": 1,
                                "duration": 229637,
                                "bracketData": 291,
                                "bracket": 20,
                                "roles": {
                                    "tanks": {"characters": []},
                                    "healers": {"characters": []},
                                    "dps": {
                                        "characters": [
                                            {
                                                "id": 42876429,
                                                "name": "Kílama",
                                                "server": {
                                                    "id": 304,
                                                    "name": "Kazzak",
                                                    "region": "EU",
                                                },
                                                "class": "Druid",
                                                "spec": "Balance",
                                                "amount": 122010.21612371,
                                                "bracketData": 291,
                                                "bracket": 20,
                                                "rank": "~3333",
                                                "best": "~1282",
                                                "totalParses": 6410,
                                                "bracketPercent": 39,
                                                "rankPercent": 48,
                                            },
                                            {
                                                "id": 42876430,
                                                "name": "Peepers",
                                                "server": {
                                                    "id": 304,
                                                    "name": "Kazzak",
                                                    "region": "EU",
                                                },
                                                "class": "Druid",
                                                "spec": "Feral",
                                                "amount": 146213.0,
                                                "bracketData": 291,
                                                "bracket": 20,
                                                "rank": "~500",
                                                "best": "~200",
                                                "totalParses": 6410,
                                                "bracketPercent": 88,
                                                "rankPercent": 88,
                                            },
                                        ]
                                    },
                                },
                            }
                        ]
                    }
                }
            }
        }
    }


# -- parse_report_rankings: the real shape ---------------------------------


def test_parses_real_shaped_response() -> None:
    result = parse_report_rankings(_real_shaped_response(), fight_id=21)

    assert result is not None
    assert result.fight_id == 21
    assert result.partition == 3
    assert result.encounter_id == 3179
    assert result.difficulty == 5
    assert result.size == 20
    assert result.kill is True
    assert result.duration_s == pytest.approx(229.637)
    assert len(result.dps) == 2


def test_parses_dps_character_fields() -> None:
    result = parse_report_rankings(_real_shaped_response(), fight_id=21)
    assert result is not None
    kilama = result.dps[0]

    assert kilama.player_name == "Kílama"
    assert kilama.server_name == "Kazzak"
    assert kilama.server_region == "EU"
    assert kilama.class_name == "Druid"
    assert kilama.spec_name == "Balance"
    assert kilama.amount == pytest.approx(122010.21612371)
    assert kilama.rank_percent == 48
    assert kilama.bracket_data == 291
    assert kilama.total_parses == 6410


def test_kill_false_when_zero() -> None:
    payload = _real_shaped_response()
    payload["data"]["reportData"]["report"]["rankings"]["data"][0]["kill"] = 0
    result = parse_report_rankings(payload, fight_id=21)
    assert result is not None
    assert result.kill is False


# -- parse_report_rankings: malformed/degraded shapes -----------------------


def test_none_when_rankings_missing() -> None:
    payload = {"data": {"reportData": {"report": {}}}}
    assert parse_report_rankings(payload, fight_id=1) is None


def test_none_when_rankings_is_not_a_dict() -> None:
    payload = {"data": {"reportData": {"report": {"rankings": None}}}}
    assert parse_report_rankings(payload, fight_id=1) is None


def test_none_when_data_list_is_empty() -> None:
    payload = {"data": {"reportData": {"report": {"rankings": {"data": []}}}}}
    assert parse_report_rankings(payload, fight_id=1) is None


def test_empty_dps_tuple_when_roles_missing() -> None:
    payload = _real_shaped_response()
    del payload["data"]["reportData"]["report"]["rankings"]["data"][0]["roles"]
    result = parse_report_rankings(payload, fight_id=21)
    assert result is not None
    assert result.dps == ()


def test_empty_dps_tuple_when_dps_characters_missing() -> None:
    payload = _real_shaped_response()
    payload["data"]["reportData"]["report"]["rankings"]["data"][0]["roles"]["dps"] = {}
    result = parse_report_rankings(payload, fight_id=21)
    assert result is not None
    assert result.dps == ()


def test_skips_character_missing_required_fields() -> None:
    payload = _real_shaped_response()
    chars = payload["data"]["reportData"]["report"]["rankings"]["data"][0]["roles"]["dps"][
        "characters"
    ]
    chars.append({"name": "NoSpec", "class": "Mage"})  # missing "spec"
    chars.append("not_a_dict")  # type: ignore[list-item]
    result = parse_report_rankings(payload, fight_id=21)
    assert result is not None
    assert len(result.dps) == 2  # the 2 well-formed characters only


def test_server_defaults_to_none_when_missing() -> None:
    payload = _real_shaped_response()
    chars = payload["data"]["reportData"]["report"]["rankings"]["data"][0]["roles"]["dps"][
        "characters"
    ]
    del chars[0]["server"]
    result = parse_report_rankings(payload, fight_id=21)
    assert result is not None
    assert result.dps[0].server_name is None
    assert result.dps[0].server_region is None


def test_rank_percent_none_when_missing() -> None:
    payload = _real_shaped_response()
    chars = payload["data"]["reportData"]["report"]["rankings"]["data"][0]["roles"]["dps"][
        "characters"
    ]
    del chars[0]["rankPercent"]
    result = parse_report_rankings(payload, fight_id=21)
    assert result is not None
    assert result.dps[0].rank_percent is None


def test_partition_none_when_missing() -> None:
    payload = _real_shaped_response()
    del payload["data"]["reportData"]["report"]["rankings"]["data"][0]["partition"]
    result = parse_report_rankings(payload, fight_id=21)
    assert result is not None
    assert result.partition is None


def test_duration_none_when_missing() -> None:
    payload = _real_shaped_response()
    del payload["data"]["reportData"]["report"]["rankings"]["data"][0]["duration"]
    result = parse_report_rankings(payload, fight_id=21)
    assert result is not None
    assert result.duration_s is None


def test_entry_not_a_dict_returns_none() -> None:
    payload = {"data": {"reportData": {"report": {"rankings": {"data": ["not_a_dict"]}}}}}
    assert parse_report_rankings(payload, fight_id=1) is None


# -- parse_report_rankings_all: T-DG.4, multi-fight ---------------------------


def _multi_fight_response() -> dict[str, Any]:
    """Mirrors the live shape measured for docs/schema_confirmado.md §13.3's
    T-DG.4 update: report.rankings with fightIDs omitted, 2 of the 6 fights
    measured live for report 2CbQfkJ47Nxq6cDT.
    """
    entry_template = _real_shaped_response()["data"]["reportData"]["report"]["rankings"]["data"][0]
    entry_2 = dict(entry_template)
    entry_2["fightID"] = 3
    entry_2["kill"] = 0
    return {
        "data": {
            "reportData": {
                "report": {"rankings": {"data": [entry_template, entry_2]}},
            }
        }
    }


def test_parse_report_rankings_all_returns_every_fight() -> None:
    results = parse_report_rankings_all(_multi_fight_response())

    assert len(results) == 2
    assert {r.fight_id for r in results} == {21, 3}


def test_parse_report_rankings_all_preserves_each_fights_own_fields() -> None:
    results = {r.fight_id: r for r in parse_report_rankings_all(_multi_fight_response())}

    assert results[21].kill is True
    assert results[3].kill is False
    assert len(results[21].dps) == 2
    assert len(results[3].dps) == 2


def test_parse_report_rankings_all_empty_when_no_data() -> None:
    payload = {"data": {"reportData": {"report": {"rankings": {"data": []}}}}}
    assert parse_report_rankings_all(payload) == ()


def test_parse_report_rankings_all_none_rankings_returns_empty_tuple() -> None:
    payload = {"data": {"reportData": {"report": {"rankings": None}}}}
    assert parse_report_rankings_all(payload) == ()


def test_parse_report_rankings_all_skips_entries_without_a_usable_fight_id() -> None:
    payload = _multi_fight_response()
    payload["data"]["reportData"]["report"]["rankings"]["data"].append({"fightID": None})
    payload["data"]["reportData"]["report"]["rankings"]["data"].append("not_a_dict")
    results = parse_report_rankings_all(payload)
    assert len(results) == 2  # the 2 well-formed entries only


def test_parse_report_rankings_all_missing_data_key_returns_empty_tuple() -> None:
    payload = {"data": {"reportData": {"report": {"rankings": {}}}}}
    assert parse_report_rankings_all(payload) == ()


# -- fetch_fight_rankings: best-effort wrapper -------------------------------


def test_fetch_fight_rankings_calls_query_fn_and_parses() -> None:
    captured: dict[str, Any] = {}

    def fake_query_fn(query: str, variables: dict[str, Any], *, op_name: str) -> dict[str, Any]:
        captured["query"] = query
        captured["variables"] = variables
        captured["op_name"] = op_name
        return _real_shaped_response()

    result = fetch_fight_rankings(fake_query_fn, report_code="2CbQfkJ47Nxq6cDT", fight_id=21)

    assert result is not None
    assert result.partition == 3
    assert captured["variables"] == {"code": "2CbQfkJ47Nxq6cDT", "fightIDs": [21]}
    assert captured["op_name"] == "fetch_report_rankings"


def test_fetch_fight_rankings_returns_none_on_api_error() -> None:
    def failing_query_fn(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise TransientApiError("boom")

    result = fetch_fight_rankings(failing_query_fn, report_code="X", fight_id=1)

    assert result is None
