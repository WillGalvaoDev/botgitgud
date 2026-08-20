from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from botgitgud.errors import RateLimitBudgetExceeded
from botgitgud.ingest.discovery_store import DiscoveryStore
from botgitgud.ingest.store import Store
from botgitgud.ingest.triage import triage_pending_reports, triage_report
from botgitgud.wcl.client import WclClient, WclClientConfig


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


def _fight_entry(fight_id: int, *, kill: bool = True, n_dps: int = 1) -> dict[str, Any]:
    characters = [
        {
            "name": f"Player{fight_id}_{i}",
            "server": {"name": "Kazzak", "region": "EU"},
            "class": "Druid",
            "spec": "Balance",
            "amount": 100000.0 + i,
            "rankPercent": 50,
            "bracketData": 291,
            "totalParses": 100,
        }
        for i in range(n_dps)
    ]
    return {
        "fightID": fight_id,
        "partition": 3,
        "encounter": {"id": 3179, "name": "Fallen-King Salhadaar"},
        "difficulty": 5,
        "size": 20,
        "kill": 1 if kill else 0,
        "duration": 100000,
        "roles": {"dps": {"characters": characters}},
    }


def _triage_response(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {"data": {"reportData": {"report": {"rankings": {"data": entries}}}}}


class _TriageTransport(httpx.BaseTransport):
    """Dispatches GetReportRankingsAllFights by report code to a canned
    response. A report code not present in `responses` fails the test
    loudly — every call this module makes must be intentional.
    """

    def __init__(
        self, responses: dict[str, dict[str, Any]], *, points_remaining: float = 3600.0
    ) -> None:
        self._responses = responses
        self._points_remaining = points_remaining
        self.calls: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if "oauth" in str(request.url):
            return _token_response()
        body = json.loads(request.content)
        query = body.get("query", "")
        if "rateLimitData" in query:
            return _rate_limit_response(points_remaining=self._points_remaining)
        if "GetReportRankingsAllFights" in query:
            code = body["variables"]["code"]
            self.calls.append(code)
            if code not in self._responses:
                pytest.fail(f"report inesperado solicitado: {code}")
            return httpx.Response(200, json=self._responses[code])
        pytest.fail(f"query GraphQL não reconhecida: {query[:80]}")


def _client(transport: _TriageTransport) -> WclClient:
    return WclClient(
        WclClientConfig(client_id="id", client_secret="secret"),
        transport=transport,
        sleep=lambda _s: None,
    )


# -- triage_report: single-report triage --------------------------------------


def test_triage_report_writes_every_fight_and_marks_the_report_triaged(tmp_path: Path) -> None:
    entries = [_fight_entry(1, n_dps=2), _fight_entry(2, kill=False, n_dps=1)]
    transport = _TriageTransport({"CODE1": _triage_response(entries)})
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_report(report_code="CODE1", zone_id=46, start_time_ms=0, end_time_ms=1)

        n_fights = triage_report(_client(transport), discovery, report_code="CODE1")

        assert n_fights == 2
        assert discovery.has_triaged_report("CODE1") is True
        assert discovery.has_fight("CODE1", 1) is True
        assert discovery.has_fight("CODE1", 2) is True

        targets = store.query(
            "SELECT count(*) AS n FROM discovery_targets WHERE report_code = 'CODE1'"
        )
    assert targets["n"][0] == 3  # 2 dps in fight 1 + 1 dps in fight 2


def test_triage_report_already_triaged_makes_zero_network_calls(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_report(report_code="CODE1", zone_id=46, start_time_ms=0, end_time_ms=1)
        discovery.mark_report_triaged("CODE1")

        transport = _TriageTransport({})  # any call fails the test
        n_fights = triage_report(_client(transport), discovery, report_code="CODE1")

    assert transport.calls == []
    assert n_fights == 0


def test_triage_report_with_zero_ranked_fights_still_marks_triaged(tmp_path: Path) -> None:
    transport = _TriageTransport({"CODE1": _triage_response([])})
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_report(report_code="CODE1", zone_id=46, start_time_ms=0, end_time_ms=1)

        n_fights = triage_report(_client(transport), discovery, report_code="CODE1")

        assert n_fights == 0
        assert discovery.has_triaged_report("CODE1") is True  # not retried forever


def test_triage_report_rate_limit_budget_exceeded_propagates_and_leaves_untriaged(
    tmp_path: Path,
) -> None:
    transport = _TriageTransport({}, points_remaining=10.0)  # below default floor
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_report(report_code="CODE1", zone_id=46, start_time_ms=0, end_time_ms=1)

        with pytest.raises(RateLimitBudgetExceeded):
            triage_report(_client(transport), discovery, report_code="CODE1")

        assert discovery.has_triaged_report("CODE1") is False


# -- triage_pending_reports: multi-report orchestration -----------------------


def test_triage_pending_reports_processes_every_untriaged_report(tmp_path: Path) -> None:
    responses = {
        "CODE1": _triage_response([_fight_entry(1)]),
        "CODE2": _triage_response([_fight_entry(2), _fight_entry(3)]),
    }
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_report(report_code="CODE1", zone_id=46, start_time_ms=0, end_time_ms=1)
        discovery.write_report(report_code="CODE2", zone_id=46, start_time_ms=0, end_time_ms=1)

        summary = triage_pending_reports(_client(_TriageTransport(responses)), discovery)

    assert summary.reports_total == 2
    assert summary.reports_triaged == 2
    assert summary.fights_written == 3
    assert summary.stopped_reason == "completed"
    assert summary.reports_remaining == 0


def test_triage_pending_reports_second_call_makes_zero_new_network_calls(tmp_path: Path) -> None:
    responses = {"CODE1": _triage_response([_fight_entry(1)])}
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_report(report_code="CODE1", zone_id=46, start_time_ms=0, end_time_ms=1)
        triage_pending_reports(_client(_TriageTransport(responses)), discovery)

        empty_transport = _TriageTransport({})
        summary = triage_pending_reports(_client(empty_transport), discovery)

    assert empty_transport.calls == []
    assert summary.reports_total == 0
    assert summary.stopped_reason == "completed"


def test_triage_pending_reports_stops_cleanly_on_rate_limit_budget(tmp_path: Path) -> None:
    transport = _TriageTransport({}, points_remaining=10.0)
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_report(report_code="CODE1", zone_id=46, start_time_ms=0, end_time_ms=1)

        summary = triage_pending_reports(_client(transport), discovery)

    assert summary.stopped_reason == "budget_exceeded"
    assert summary.reports_triaged == 0


def test_triage_pending_reports_respects_max_points(tmp_path: Path) -> None:
    responses = {
        "CODE1": _triage_response([_fight_entry(1)]),
        "CODE2": _triage_response([_fight_entry(2)]),
    }
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_report(report_code="CODE1", zone_id=46, start_time_ms=0, end_time_ms=1)
        discovery.write_report(report_code="CODE2", zone_id=46, start_time_ms=0, end_time_ms=1)

        summary = triage_pending_reports(
            _client(_TriageTransport(responses)), discovery, max_points=2.0
        )

    assert summary.stopped_reason == "max_points_reached"
    assert summary.reports_triaged == 1
    assert summary.reports_remaining == 1


def test_triage_pending_reports_filters_by_zone(tmp_path: Path) -> None:
    responses = {"CODE1": _triage_response([_fight_entry(1)])}
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_report(report_code="CODE1", zone_id=46, start_time_ms=0, end_time_ms=1)
        discovery.write_report(report_code="CODE2", zone_id=99, start_time_ms=0, end_time_ms=1)

        summary = triage_pending_reports(
            _client(_TriageTransport(responses)), discovery, zone_id=46
        )

    assert summary.reports_total == 1
    assert summary.reports_triaged == 1
