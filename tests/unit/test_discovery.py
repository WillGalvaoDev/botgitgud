from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from botgitgud.errors import RateLimitBudgetExceeded
from botgitgud.ingest.discovery import (
    DEFAULT_WINDOW_SPAN_MS,
    MAX_DISCOVERY_PAGE,
    default_job_key,
    discover_reports_in_window,
    run_discovery,
)
from botgitgud.ingest.discovery_store import DiscoveryStore
from botgitgud.ingest.store import Store
from botgitgud.wcl.client import WclClient, WclClientConfig

ZONE = 46
JOB_KEY = default_job_key(ZONE)


def _token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})


def _rate_limit_response(*, points_remaining: float = 3600.0) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": {
                "rateLimitData": {
                    "limitPerHour": 3600,
                    "pointsSpentThisHour": 3600.0 - points_remaining,
                    "pointsResetIn": 3600,
                }
            }
        },
    )


def _discover_page(rows: list[tuple[str, int, int]], *, has_more: bool) -> dict[str, Any]:
    return {
        "data": {
            "reportData": {
                "reports": {
                    "has_more_pages": has_more,
                    "data": [{"code": c, "startTime": s, "endTime": e} for c, s, e in rows],
                }
            }
        }
    }


class _DiscoverTransport(httpx.BaseTransport):
    """Dispatches DiscoverReports by page number to a canned response dict.
    A page not present in `pages` fails the test loudly rather than 404ing
    silently — every network call this module makes must be intentional.
    """

    def __init__(
        self, pages: dict[int, dict[str, Any]], *, points_remaining: float = 3600.0
    ) -> None:
        self._pages = pages
        self._points_remaining = points_remaining
        self.calls: list[int] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if "oauth" in str(request.url):
            return _token_response()
        body = json.loads(request.content)
        query = body.get("query", "")
        if "rateLimitData" in query:
            return _rate_limit_response(points_remaining=self._points_remaining)
        if "DiscoverReports" in query:
            page = body["variables"]["page"]
            self.calls.append(page)
            if page not in self._pages:
                pytest.fail(f"página inesperada solicitada: {page}")
            return httpx.Response(200, json=self._pages[page])
        pytest.fail(f"query GraphQL não reconhecida: {query[:80]}")


def _client(transport: _DiscoverTransport) -> WclClient:
    return WclClient(
        WclClientConfig(client_id="id", client_secret="secret"),
        transport=transport,
        sleep=lambda _s: None,
    )


# -- discover_reports_in_window: paging + checkpointing ----------------------


def test_pages_and_persists_checkpoint_after_each_page(tmp_path: Path) -> None:
    pages = {
        1: _discover_page([("R1", 100, 200), ("R2", 150, 250)], has_more=True),
        2: _discover_page([("R3", 300, 400)], has_more=False),
    }
    transport = _DiscoverTransport(pages)
    with Store(tmp_path) as store:
        discovery_store = DiscoveryStore(store)
        result = discover_reports_in_window(
            _client(transport),
            discovery_store,
            job_key=JOB_KEY,
            zone_id=ZONE,
            window_start=0,
            window_end=1000,
        )

        assert result.state == "done"
        assert result.last_page == 2
        assert result.n_reports_written == 3
        assert discovery_store.count_reports(zone_id=ZONE) == 3

        checkpoint = discovery_store.read_checkpoint(JOB_KEY, 0, 1000)
    assert checkpoint is not None
    assert checkpoint.state == "done"
    assert checkpoint.last_page == 2
    assert transport.calls == [1, 2]


def test_resumes_without_repeating_a_completed_page(tmp_path: Path) -> None:
    """Killing the process after page 1 and re-running must not re-request
    page 1 — only page 2 onward.
    """
    with Store(tmp_path) as store:
        discovery_store = DiscoveryStore(store)
        discovery_store.write_checkpoint(
            job_key=JOB_KEY,
            zone_id=ZONE,
            window_start=0,
            window_end=1000,
            last_page=1,
            state="in_progress",
            points_spent=1.0,
        )

        # Page 1 deliberately absent — requesting it would fail the test.
        pages = {2: _discover_page([("R2", 150, 250)], has_more=False)}
        transport = _DiscoverTransport(pages)
        result = discover_reports_in_window(
            _client(transport),
            discovery_store,
            job_key=JOB_KEY,
            zone_id=ZONE,
            window_start=0,
            window_end=1000,
        )

    assert transport.calls == [2]
    assert result.state == "done"
    assert result.last_page == 2


def test_a_window_already_done_makes_zero_network_calls(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery_store = DiscoveryStore(store)
        discovery_store.write_checkpoint(
            job_key=JOB_KEY,
            zone_id=ZONE,
            window_start=0,
            window_end=1000,
            last_page=3,
            state="done",
            points_spent=3.0,
        )

        transport = _DiscoverTransport({})  # any call fails the test
        result = discover_reports_in_window(
            _client(transport),
            discovery_store,
            job_key=JOB_KEY,
            zone_id=ZONE,
            window_start=0,
            window_end=1000,
        )

    assert transport.calls == []
    assert result.state == "done"
    assert result.n_reports_written == 0


def test_an_exhausted_cap_window_also_makes_zero_network_calls(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery_store = DiscoveryStore(store)
        discovery_store.write_checkpoint(
            job_key=JOB_KEY,
            zone_id=ZONE,
            window_start=0,
            window_end=1000,
            last_page=MAX_DISCOVERY_PAGE,
            state="exhausted_cap",
            points_spent=25.0,
        )

        transport = _DiscoverTransport({})
        result = discover_reports_in_window(
            _client(transport),
            discovery_store,
            job_key=JOB_KEY,
            zone_id=ZONE,
            window_start=0,
            window_end=1000,
        )

    assert transport.calls == []
    assert result.state == "exhausted_cap"


# -- page-25 cap subdivision --------------------------------------------------


def test_hitting_page_25_with_more_data_marks_exhausted_cap_and_subdivides(
    tmp_path: Path,
) -> None:
    pages = {p: _discover_page([(f"R{p}", p, p + 1)], has_more=True) for p in range(1, 26)}
    transport = _DiscoverTransport(pages)
    with Store(tmp_path) as store:
        discovery_store = DiscoveryStore(store)
        result = discover_reports_in_window(
            _client(transport),
            discovery_store,
            job_key=JOB_KEY,
            zone_id=ZONE,
            window_start=0,
            window_end=1_000_000,
        )

        checkpoints = discovery_store.list_checkpoints(JOB_KEY)

    assert transport.calls == list(range(1, 26))  # never requested page 26
    assert result.state == "exhausted_cap"
    assert result.last_page == MAX_DISCOVERY_PAGE

    # parent window (exhausted_cap) + 2 pending half-span children
    assert len(checkpoints) == 3
    parent = next(c for c in checkpoints if c.window_start == 0 and c.window_end == 1_000_000)
    assert parent.state == "exhausted_cap"
    children = [c for c in checkpoints if c is not parent]
    assert {c.state for c in children} == {"pending"}
    assert {c.window_start for c in children} == {0, 500_000}
    assert {c.window_end for c in children} == {500_000, 1_000_000}


def test_subdividing_an_already_subdivided_window_does_not_duplicate_children(
    tmp_path: Path,
) -> None:
    pages = {p: _discover_page([], has_more=True) for p in range(1, 26)}
    with Store(tmp_path) as store:
        discovery_store = DiscoveryStore(store)
        discover_reports_in_window(
            _client(_DiscoverTransport(pages)),
            discovery_store,
            job_key=JOB_KEY,
            zone_id=ZONE,
            window_start=0,
            window_end=1000,
        )
        # Force the same parent window to hit the cap again (e.g. a retried
        # run before the child windows were processed) — must not duplicate
        # the pending children it already queued.
        discovery_store.write_checkpoint(
            job_key=JOB_KEY,
            zone_id=ZONE,
            window_start=0,
            window_end=1000,
            last_page=24,
            state="in_progress",
            points_spent=24.0,
        )
        discover_reports_in_window(
            _client(_DiscoverTransport({25: _discover_page([], has_more=True)})),
            discovery_store,
            job_key=JOB_KEY,
            zone_id=ZONE,
            window_start=0,
            window_end=1000,
        )

        checkpoints = discovery_store.list_checkpoints(JOB_KEY)
    assert len(checkpoints) == 3  # still parent + 2 children, not 5


# -- RateLimitBudgetExceeded: clean stop, checkpoint intact -------------------


def test_rate_limit_budget_exceeded_leaves_checkpoint_at_last_completed_page(
    tmp_path: Path,
) -> None:
    transport = _DiscoverTransport({}, points_remaining=10.0)  # below the 1000 default floor
    with Store(tmp_path) as store:
        discovery_store = DiscoveryStore(store)
        with pytest.raises(RateLimitBudgetExceeded):
            discover_reports_in_window(
                _client(transport),
                discovery_store,
                job_key=JOB_KEY,
                zone_id=ZONE,
                window_start=0,
                window_end=1000,
            )

        checkpoint = discovery_store.read_checkpoint(JOB_KEY, 0, 1000)
    assert checkpoint is not None
    assert checkpoint.state == "in_progress"
    assert checkpoint.last_page == 0  # no page completed yet
    assert transport.calls == []  # budget check itself blocked the query


def test_rate_limit_budget_exceeded_mid_window_preserves_completed_pages(
    tmp_path: Path,
) -> None:
    with Store(tmp_path) as store:
        discovery_store = DiscoveryStore(store)
        discovery_store.write_checkpoint(
            job_key=JOB_KEY,
            zone_id=ZONE,
            window_start=0,
            window_end=1000,
            last_page=2,
            state="in_progress",
            points_spent=2.0,
        )
        transport = _DiscoverTransport({}, points_remaining=10.0)
        with pytest.raises(RateLimitBudgetExceeded):
            discover_reports_in_window(
                _client(transport),
                discovery_store,
                job_key=JOB_KEY,
                zone_id=ZONE,
                window_start=0,
                window_end=1000,
            )

        checkpoint = discovery_store.read_checkpoint(JOB_KEY, 0, 1000)
    assert checkpoint is not None
    assert checkpoint.last_page == 2  # unchanged — still at the last real completion


# -- run_discovery: multi-window orchestration --------------------------------


def test_run_discovery_processes_every_window_in_a_range(tmp_path: Path) -> None:
    window_span = 1000
    pages = {1: _discover_page([("R1", 0, 1)], has_more=False)}
    with Store(tmp_path) as store:
        discovery_store = DiscoveryStore(store)
        summary = run_discovery(
            _client(_DiscoverTransport(pages)),
            discovery_store,
            zone_id=ZONE,
            start_ms=0,
            end_ms=2000,
            window_span_ms=window_span,
        )

    assert summary.windows_total == 2
    assert summary.windows_done == 2
    assert summary.stopped_reason == "completed"
    assert summary.windows_remaining == 0


def test_run_discovery_second_call_makes_zero_new_network_calls(tmp_path: Path) -> None:
    pages = {1: _discover_page([("R1", 0, 1)], has_more=False)}
    with Store(tmp_path) as store:
        discovery_store = DiscoveryStore(store)
        run_discovery(
            _client(_DiscoverTransport(pages)),
            discovery_store,
            zone_id=ZONE,
            start_ms=0,
            end_ms=1000,
            window_span_ms=1000,
        )

        empty_transport = _DiscoverTransport({})  # any call fails the test
        summary = run_discovery(
            _client(empty_transport),
            discovery_store,
            zone_id=ZONE,
            start_ms=0,
            end_ms=1000,
            window_span_ms=1000,
        )

    assert empty_transport.calls == []
    assert summary.windows_done == 1
    assert summary.stopped_reason == "completed"


def test_run_discovery_stops_cleanly_on_rate_limit_budget(tmp_path: Path) -> None:
    transport = _DiscoverTransport({}, points_remaining=10.0)
    with Store(tmp_path) as store:
        discovery_store = DiscoveryStore(store)
        summary = run_discovery(
            _client(transport),
            discovery_store,
            zone_id=ZONE,
            start_ms=0,
            end_ms=2000,
            window_span_ms=1000,
        )

    assert summary.stopped_reason == "budget_exceeded"
    assert summary.windows_done == 0


def test_run_discovery_respects_max_points(tmp_path: Path) -> None:
    """The cap is checked BEFORE starting each new window (never mid-window
    — a window is always finished once begun), so with max_points equal to
    exactly one window's cost (1.0 pt/page), the first window completes and
    the second is never started.
    """
    pages = {1: _discover_page([("R1", 0, 1)], has_more=False)}
    with Store(tmp_path) as store:
        discovery_store = DiscoveryStore(store)
        summary = run_discovery(
            _client(_DiscoverTransport(pages)),
            discovery_store,
            zone_id=ZONE,
            start_ms=0,
            end_ms=3000,
            window_span_ms=1000,
            max_points=1.0,
        )

    assert summary.stopped_reason == "max_points_reached"
    assert summary.windows_done == 1
    assert summary.windows_remaining == 2


def test_run_discovery_uses_default_job_key_derived_from_zone(tmp_path: Path) -> None:
    pages = {1: _discover_page([], has_more=False)}
    with Store(tmp_path) as store:
        discovery_store = DiscoveryStore(store)
        summary = run_discovery(
            _client(_DiscoverTransport(pages)),
            discovery_store,
            zone_id=ZONE,
            start_ms=0,
            end_ms=1000,
            window_span_ms=1000,
        )

    assert summary.job_key == f"discover:zone={ZONE}"


def test_default_window_span_is_twelve_hours() -> None:
    assert DEFAULT_WINDOW_SPAN_MS == 12 * 3600 * 1000
