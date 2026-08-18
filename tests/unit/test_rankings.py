from __future__ import annotations

from typing import Any, cast

import pytest

from botgitgud.analysis.cohort import COHORT_MIN_HARD, MAX_RANKING_PAGES
from botgitgud.errors import InsufficientCohort
from botgitgud.ingest.log_fetcher import LogRequest
from botgitgud.ingest.rankings import RankingCandidate, fetch_cohort_logs, fetch_ranking_candidates


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
