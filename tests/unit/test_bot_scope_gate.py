"""T0.9 — cost test: an out-of-scope spec must never trigger a ranking
query. Exercises the real bot.py (not legacy), via the isolated runner
(D-4) with a counting httpx transport injected in place of the real
network, and fetch_player_timeline_data monkeypatched to skip the network
entirely for the (canned) user data — this test cares only about what
happens *after* the spec is known.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from new_bot_runner import isolated_new_bot


def _canned_user_data(class_name: str, spec_name: str) -> dict[str, Any]:
    return {
        "fight": {
            "fight_id": 1,
            "encounter_id": 3179,
            "boss_name": "Fallen-King Salhadaar",
            "duration_sec": 300.0,
            "difficulty": 5,
        },
        "build": {
            "class": class_name,
            "spec": spec_name,
            "server": "Azralon",
            "region": "US",
        },
        "timeline": {},
        "dps": 100000.0,
    }


class _CountingTransport(httpx.BaseTransport):
    """Never actually sent anywhere real in this test — if this fires even
    once, the scope gate let an API call slip through for a rejected spec.
    """

    def __init__(self) -> None:
        self.requests: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(f"{request.method} {request.url}")
        pytest.fail(f"chamada de API inesperada para spec fora de escopo: {self.requests[-1]}")


def _run_with_counting_transport(
    class_name: str, spec_name: str, scratch_dir: Path
) -> tuple[dict[str, Any], list[str]]:
    with isolated_new_bot(scratch_dir) as _new_bot:
        new_bot = cast(Any, _new_bot)
        transport = _CountingTransport()
        new_bot._wcl_client = new_bot.WclClient(
            new_bot.WclClientConfig(client_id="test", client_secret="test"),
            transport=transport,
        )
        new_bot._blizzard_client = new_bot.BlizzardClient(
            new_bot.BlizzardClientConfig(client_id="test", client_secret="test"),
            transport=transport,
        )

        canned = _canned_user_data(class_name, spec_name)
        new_bot.fetch_player_timeline_data = lambda *_args, **_kw: canned

        result = new_bot.run_analysis("FAKECODE0000000", 1, "Fakename")
        return result, transport.requests


def test_tank_spec_triggers_zero_ranking_queries(tmp_path: Path) -> None:
    result, requests_made = _run_with_counting_transport("Warrior", "Protection", tmp_path)
    assert requests_made == []
    assert result.get("scope_rejection") is not None
    assert "tanks" in result["scope_rejection"]
    assert result.get("matched") == 0


def test_healer_spec_triggers_zero_ranking_queries(tmp_path: Path) -> None:
    result, requests_made = _run_with_counting_transport("Priest", "Discipline", tmp_path)
    assert requests_made == []
    assert "healers" in (result.get("scope_rejection") or "")


def test_augmentation_evoker_triggers_zero_ranking_queries(tmp_path: Path) -> None:
    result, requests_made = _run_with_counting_transport("Evoker", "Augmentation", tmp_path)
    assert requests_made == []
    assert "atribuída a outros" in (result.get("scope_rejection") or "")


def test_unknown_spec_triggers_zero_ranking_queries(tmp_path: Path) -> None:
    result, requests_made = _run_with_counting_transport("Mage", "Chronomancer", tmp_path)
    assert requests_made == []
    assert "não reconhecida" in (result.get("scope_rejection") or "")
