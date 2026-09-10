from __future__ import annotations

import json
from typing import Any, cast

import httpx
import httpx_cassette_transport as cassette_transport
import pytest
from http_cassette import Cassette

from botgitgud.analysis.cohort import COHORT_MIN_HARD, MAX_RANKING_PAGES
from botgitgud.errors import DataError, InsufficientCohort, RateLimitBudgetExceeded
from botgitgud.ingest.log_fetcher import LogRequest
from botgitgud.ingest.rankings import (
    RankingCandidate,
    fetch_cohort_logs,
    fetch_ranking_candidates,
    get_current_partition,
)
from botgitgud.wcl.queries import QUERY_RANKINGS_PAGE


def _ranking(
    name: str, duration_s: float, code: str = "ABCDEFGHIJKLMNOP", fight_id: int = 1
) -> dict[str, Any]:
    return {
        "name": name,
        "duration": duration_s * 1000,
        "report": {"code": code, "fightID": fight_id},
    }


def _page(rankings: list[dict[str, Any]], *, has_more: bool) -> dict[str, Any]:
    return {
        "data": {
            "worldData": {
                "encounter": {"characterRankings": {"rankings": rankings, "hasMorePages": has_more}}
            }
        }
    }


class _FakeClient:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._pages = pages
        self.calls = 0
        self.last_variables: dict[str, Any] = {}

    def query(self, _query: str, variables: dict[str, Any], *, op_name: str) -> dict[str, Any]:
        del op_name
        self.last_variables = variables
        page = self._pages[self.calls]
        self.calls += 1
        return page


def _fetch(client: Any, **overrides: Any) -> list[RankingCandidate]:
    defaults: dict[str, Any] = {
        "encounter_id": 3179,
        "class_name": "Warlock",
        "spec_name": "Demonology",
        "partition": 3,
        "difficulty": 5,
        "target_duration_s": 300.0,
    }
    defaults.update(overrides)
    return fetch_ranking_candidates(client, **defaults)


def test_returns_candidates_within_sanity_band() -> None:
    rankings = [_ranking(f"P{i}", 300.0) for i in range(10)]
    client = _FakeClient([_page(rankings, has_more=False)])
    candidates = _fetch(client)
    assert len(candidates) == 10
    assert client.calls == 1
    assert candidates[0].report_code == "ABCDEFGHIJKLMNOP"


def test_filters_out_candidates_outside_sanity_band() -> None:
    within = [_ranking(f"P{i}", 300.0) for i in range(8)]
    outside = [_ranking("Outlier", 1000.0)]  # far past ±35% of 300s
    client = _FakeClient([_page(within + outside, has_more=False)])
    candidates = _fetch(client)
    assert len(candidates) == 8
    assert all(c.player_name != "Outlier" for c in candidates)


def test_pagination_continues_while_has_more_pages() -> None:
    page1 = _page([_ranking(f"P{i}", 300.0) for i in range(5)], has_more=True)
    page2 = _page([_ranking(f"Q{i}", 300.0) for i in range(5)], has_more=False)
    client = _FakeClient([page1, page2])
    candidates = _fetch(client)
    assert len(candidates) == 10
    assert client.calls == 2


def test_raises_insufficient_cohort_when_too_few_candidates() -> None:
    rankings = [_ranking(f"P{i}", 300.0) for i in range(3)]
    client = _FakeClient([_page(rankings, has_more=False)])
    with pytest.raises(InsufficientCohort) as exc_info:
        _fetch(client)
    assert exc_info.value.n_members == 3
    assert exc_info.value.minimum_required == COHORT_MIN_HARD


def test_stops_at_max_ranking_pages_even_if_more_available() -> None:
    pages = [
        _page([_ranking(f"P{p}_{i}", 300.0) for i in range(3)], has_more=True) for p in range(20)
    ]
    client = _FakeClient(pages)
    candidates = _fetch(client)
    assert client.calls == MAX_RANKING_PAGES
    assert len(candidates) == 3 * MAX_RANKING_PAGES


def test_class_name_prefix_stripped_from_spec_name_when_present() -> None:
    client = _FakeClient([_page([_ranking(f"P{i}", 300.0) for i in range(8)], has_more=False)])
    _fetch(client, class_name="Warlock", spec_name="WarlockDemonology")
    assert client.last_variables["className"] == "Warlock"
    assert client.last_variables["specName"] == "Demonology"


def test_spec_name_left_alone_when_it_does_not_contain_class_name() -> None:
    client = _FakeClient([_page([_ranking(f"P{i}", 300.0) for i in range(8)], has_more=False)])
    _fetch(client, class_name="Hunter", spec_name="Beast Mastery")
    assert client.last_variables["specName"] == "Beast Mastery"


def test_partition_is_passed_through_to_the_query() -> None:
    client = _FakeClient([_page([_ranking(f"P{i}", 300.0) for i in range(8)], has_more=False)])
    _fetch(client, partition=7)
    assert client.last_variables["partition"] == 7


def test_difficulty_is_passed_through_to_the_query() -> None:
    client = _FakeClient([_page([_ranking(f"P{i}", 300.0) for i in range(8)], has_more=False)])
    _fetch(client, difficulty=4)
    assert client.last_variables["difficulty"] == 4


def test_difficulty_is_a_required_argument() -> None:
    client = _FakeClient([_page([], has_more=False)])
    with pytest.raises(TypeError):
        fetch_ranking_candidates(  # type: ignore[call-arg]
            client,
            encounter_id=3179,
            class_name="Warlock",
            spec_name="Demonology",
            partition=3,
            target_duration_s=300.0,
        )


def _ranking_cassette_request(difficulty: int) -> httpx.Request:
    return httpx.Request(
        "POST",
        "https://www.warcraftlogs.com/api/v2/client",
        content=json.dumps(
            {
                "query": QUERY_RANKINGS_PAGE,
                "variables": {
                    "encounterID": 3179,
                    "className": "Warlock",
                    "specName": "Demonology",
                    "page": 1,
                    "partition": 3,
                    "difficulty": difficulty,
                },
            }
        ).encode(),
    )


def test_rankings_cassette_shim_resolves_mythic_only(monkeypatch: pytest.MonkeyPatch) -> None:
    def load(_key: str) -> Cassette | None:
        # Only the legacy shape exists: no variable and no query argument.
        return Cassette("POST", "unused", None, {}, 200, {"legacy": True}) if calls > 1 else None

    calls = 0

    def counting_load(key: str) -> Cassette | None:
        nonlocal calls
        calls += 1
        return load(key)

    monkeypatch.setattr(cassette_transport, "load_cassette", counting_load)
    response = cassette_transport.ReplayTransport().handle_request(_ranking_cassette_request(5))
    assert response.json() == {"legacy": True}
    assert calls == 2


def test_rankings_cassette_shim_fails_closed_for_heroic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cassette_transport, "load_cassette", lambda _key: None)
    with pytest.raises(pytest.fail.Exception, match="Cassete ausente"):
        cassette_transport.ReplayTransport().handle_request(_ranking_cassette_request(4))


def test_rate_limit_budget_exceeded_propagates_not_swallowed_as_page_failure() -> None:
    """T1.7: unlike a transient page error, RateLimitBudgetExceeded must
    propagate out (so build-cohort can exit 75), never be treated as
    'this page failed, use what we have so far'.
    """

    class _BudgetExceededClient:
        def query(self, _query: str, _variables: dict[str, Any], *, op_name: str) -> dict[str, Any]:
            del op_name
            raise RateLimitBudgetExceeded(
                "orçamento excedido", points_remaining=10.0, reset_in_seconds=60.0
            )

    with pytest.raises(RateLimitBudgetExceeded):
        _fetch(cast(Any, _BudgetExceededClient()))


def test_target_duration_none_keeps_every_candidate_regardless_of_duration() -> None:
    """T1.7's batch build-cohort mode: no sanity-band filter at all."""
    rankings = [_ranking(f"P{i}", 300.0) for i in range(4)] + [
        _ranking(f"Q{i}", 5000.0) for i in range(4)
    ]
    client = _FakeClient([_page(rankings, has_more=False)])
    candidates = _fetch(client, target_duration_s=None)
    assert len(candidates) == 8


# -- get_current_partition -------------------------------------------------------


def _zone_page(partitions: list[dict[str, Any]]) -> dict[str, Any]:
    return {"data": {"worldData": {"encounter": {"zone": {"id": 46, "partitions": partitions}}}}}


def test_get_current_partition_returns_the_default_one() -> None:
    client = _FakeClient(
        [
            _zone_page(
                [
                    {"id": 1, "default": False},
                    {"id": 3, "default": True},
                    {"id": 4, "default": False},
                ]
            )
        ]
    )
    assert get_current_partition(cast(Any, client), 3179) == 3


def test_get_current_partition_raises_when_no_default_marked() -> None:
    client = _FakeClient([_zone_page([{"id": 1, "default": False}])])
    with pytest.raises(DataError):
        get_current_partition(cast(Any, client), 3179)


# -- fetch_cohort_logs -----------------------------------------------------------


class _FakeFetcher:
    def __init__(self, result: list[str]) -> None:
        self._result = result
        self.calls: tuple[list[LogRequest], int] | None = None

    def fetch_many(self, refs: list[LogRequest], *, max_workers: int) -> list[str]:
        self.calls = (refs, max_workers)
        return self._result


def test_fetch_cohort_logs_converts_candidates_to_log_requests() -> None:
    fake = _FakeFetcher(["log1", "log2"])
    candidates = [
        RankingCandidate("ABCDEFGHIJKLMNOP", 1, "P1", 300.0),
        RankingCandidate("QRSTUVWXYZ123456", 2, "P2", 305.0),
    ]
    result = fetch_cohort_logs(cast(Any, fake), candidates, max_workers=3)

    assert result == ["log1", "log2"]
    assert fake.calls is not None
    refs, max_workers = fake.calls
    assert max_workers == 3
    assert refs == [
        LogRequest("ABCDEFGHIJKLMNOP", 1, "P1"),
        LogRequest("QRSTUVWXYZ123456", 2, "P2"),
    ]
