"""M3.1 — required real-recorded evidence (docs/m3-1-specification.md §5),
answering independent review finding R6: a reproducible replay of the real
Buffs/Debuffs and Resources cassettes in `tests/fixtures/cassettes/`, with
an oracle computed directly from the raw JSON (never by re-using the code
under test), plus empty/absent/second-page-error derivatives built from
that same real data. This operationalizes the census
`parse_aura_table_provenance`'s own docstring claims, as a permanent,
re-runnable test rather than a one-off pre-implementation script.

Any change to the recorded cassettes that breaks this file is exactly the
signal D-M31-03's census promise exists to catch.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from botgitgud.domain.models import CollectionStatus
from botgitgud.ingest.performance_parsing import (
    parse_aura_table_provenance,
    parse_resource_type_counts,
)

_CASSETTES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "cassettes"


def _load_cassettes() -> list[dict[str, Any]]:
    cassettes = []
    for path in sorted(_CASSETTES_DIR.glob("*.json")):
        cassettes.append(json.loads(path.read_text(encoding="utf-8")))
    return cassettes


def _table_data(cassette: dict[str, Any]) -> Any:
    return (
        cassette["response_json"]
        .get("data", {})
        .get("reportData", {})
        .get("report", {})
        .get("table", {})
        .get("data", {})
    )


def _aura_cassettes() -> list[dict[str, Any]]:
    result = []
    for cassette in _load_cassettes():
        query = (cassette.get("request_payload") or {}).get("query", "")
        if "GetPlayerBuffs" in query or "GetPlayerDebuffs" in query:
            result.append(cassette)
    return result


def _resource_cassettes() -> list[dict[str, Any]]:
    result = []
    for cassette in _load_cassettes():
        query = (cassette.get("request_payload") or {}).get("query", "")
        if "GetPlayerResourceEvents" in query:
            result.append(cassette)
    return result


def _oracle_auras(table_data: Any) -> dict[int, tuple[float, int]]:
    """Independent numerical oracle for ONE aura table's raw JSON: guid ->
    (totalUptime ms, totalUses), computed here directly from `table_data`,
    never by calling `parse_aura_table_provenance` (the function under
    test). Applies the same "drop a guid whose representations disagree"
    rule as D-M31-08/R5 independently (a real cassette check confirms no
    recorded table has a duplicate guid at all, so this never actually
    triggers against real data — it exists so the oracle stays correct
    if a future cassette ever does).
    """
    seen: dict[int, set[tuple[float, int]]] = {}
    for a in table_data.get("auras", []):
        if not isinstance(a, dict) or "guid" not in a:
            continue
        uptime_ms = a.get("totalUptime")
        if uptime_ms is None:
            continue
        seen.setdefault(int(a["guid"]), set()).add((float(uptime_ms), int(a.get("totalUses") or 0)))
    return {guid: next(iter(values)) for guid, values in seen.items() if len(values) == 1}


def _fight_duration_index() -> dict[tuple[str, int], float]:
    """Independent source of the fight's own duration: `fights[].startTime`/
    `endTime` from whichever cassette recorded them (GetPlayerMeta) — the
    EXACT source `ingest/log_fetcher.py:271-273` uses for `duration_s` in
    production. Never derived from an aura table's own `totalTime`.
    """
    index: dict[tuple[str, int], float] = {}
    for cassette in _load_cassettes():
        variables = (cassette.get("request_payload") or {}).get("variables") or {}
        code = variables.get("code")
        if not isinstance(code, str):
            continue
        report = cassette["response_json"].get("data", {}).get("reportData", {}).get("report", {})
        fights = report.get("fights") if isinstance(report, dict) else None
        if not isinstance(fights, list):
            continue
        for fight in fights:
            if not isinstance(fight, dict) or "id" not in fight:
                continue
            start, end = fight.get("startTime"), fight.get("endTime")
            if isinstance(start, (int, float)) and isinstance(end, (int, float)):
                index[(code, fight["id"])] = float(end) - float(start)
    return index


# -- census: every recorded aura table is COMPLETE against the real fight duration --


def test_census_every_recorded_aura_table_is_complete_against_the_real_fight_duration() -> None:
    """This is a CONTENT proof, not just a coverage/status one: the
    independent review confirmed a mutation that replaces every parsed
    `auras` mapping with `{}` (erasing all aura readings while keeping
    coverage/status intact) previously left all corpus-replay tests
    green. Comparing `provenance.auras` against `_oracle_auras` (computed
    independently from the same raw JSON) fails immediately under that
    mutation, on the very first cassette — real cassette data has no
    empty aura table (2024 aura entries total across the 50), so an
    always-empty parser output can never match the oracle.
    """
    aura_cassettes = _aura_cassettes()
    assert len(aura_cassettes) == 50, "the recorded cassette set changed size; re-run the census"
    duration_index = _fight_duration_index()

    checked = 0
    total_oracle_auras = 0
    for cassette in aura_cassettes:
        variables = cassette["request_payload"]["variables"]
        code = variables["code"]
        (fight_id,) = variables["fightIDs"]
        duration_ms = duration_index[(code, fight_id)]
        table_data = _table_data(cassette)
        oracle_auras = _oracle_auras(table_data)
        provenance = parse_aura_table_provenance(table_data, duration_ms)
        assert provenance.collection.status is CollectionStatus.COMPLETE, (
            code,
            fight_id,
            provenance.collection,
        )
        assert provenance.collection.requested_start_ms == 0.0
        assert provenance.collection.requested_end_ms == duration_ms
        assert provenance.total_time_ms == duration_ms
        # The content proof: every real aura reading in this table must
        # survive parsing, unaltered, matching the independent oracle.
        assert provenance.auras == oracle_auras, (code, fight_id)
        assert oracle_auras, (code, fight_id, "real cassette unexpectedly has no auras")
        total_oracle_auras += len(oracle_auras)
        checked += 1
    assert checked == 50
    assert total_oracle_auras == 2024, "recorded cassette aura content changed; re-run the census"


def test_census_real_aura_table_with_a_wrong_duration_is_partial_not_complete() -> None:
    # Derived-from-real: same real response, a duration that is NOT this
    # fight's own — must not be silently trusted (D-M31-03's own negative
    # case, exercised on real recorded data rather than only synthetic).
    aura_cassettes = _aura_cassettes()
    duration_index = _fight_duration_index()
    cassette = aura_cassettes[0]
    variables = cassette["request_payload"]["variables"]
    code = variables["code"]
    (fight_id,) = variables["fightIDs"]
    real_duration_ms = duration_index[(code, fight_id)]
    wrong_duration_ms = real_duration_ms + 1.0
    provenance = parse_aura_table_provenance(_table_data(cassette), wrong_duration_ms)
    assert provenance.collection.status is CollectionStatus.PARTIAL
    assert provenance.collection.reasons == ("AURA_TABLE_TOTAL_TIME_MISMATCH",)


# -- derived-empty: a real table's own totalTime, auras emptied ----------------


def test_derived_empty_aura_table_from_a_real_response_is_complete_with_no_auras() -> None:
    aura_cassettes = _aura_cassettes()
    duration_index = _fight_duration_index()
    cassette = aura_cassettes[0]
    variables = cassette["request_payload"]["variables"]
    code, (fight_id,) = variables["code"], variables["fightIDs"]
    duration_ms = duration_index[(code, fight_id)]
    real_table = _table_data(cassette)
    empty_table = {**real_table, "auras": []}
    provenance = parse_aura_table_provenance(empty_table, duration_ms)
    assert provenance.collection.status is CollectionStatus.COMPLETE
    assert provenance.auras == {}


# -- derived-absent: a real cassette response with the table itself dropped ----


def test_derived_absent_aura_table_from_a_real_response_is_unavailable() -> None:
    """Derived from a real cassette (re-review.md's own complaint: the
    prior version called `parse_aura_table_provenance(None, ...)` with no
    cassette involved at all). Takes one real recorded Buffs/Debuffs
    response and simulates the shape a malformed/absent-table API reply
    would have — `report.table` present but with no `data` key — rather
    than a hand-built `None`.
    """
    aura_cassettes = _aura_cassettes()
    duration_index = _fight_duration_index()
    cassette = aura_cassettes[0]
    variables = cassette["request_payload"]["variables"]
    code, (fight_id,) = variables["code"], variables["fightIDs"]
    duration_ms = duration_index[(code, fight_id)]

    real_table_wrapper = cassette["response_json"]["data"]["reportData"]["report"]["table"]
    assert "data" in real_table_wrapper, "expected shape changed; this derivation no longer applies"
    # The real response's own "table" wrapper with "data" removed — the
    # exact shape a genuinely absent table has — read back through the
    # SAME accessor `ingest/log_fetcher_aux.py` uses in production
    # (`.get("table", {}).get("data", {})`).
    absent_table_wrapper = {k: v for k, v in real_table_wrapper.items() if k != "data"}
    absent_table_data = absent_table_wrapper.get("data", {})
    provenance = parse_aura_table_provenance(absent_table_data, duration_ms)
    assert provenance.collection.status is CollectionStatus.UNKNOWN
    assert provenance.collection.reasons == ("STREAM_UNAVAILABLE",)
    assert provenance.auras == {}


# -- resource oracle: independent sum straight from the raw cassette JSON ------


def _oracle_resource_counts(
    events: list[dict[str, Any]], player_id: int
) -> dict[int, tuple[int, float]]:
    """Independent numerical oracle for one player's own resourcechange
    events, computed here directly from raw JSON — never by calling
    `parse_resource_type_counts` (the function under test).
    """
    oracle_counts: dict[int, int] = {}
    oracle_waste: dict[int, float] = {}
    for e in events:
        if (
            not isinstance(e, dict)
            or e.get("type") != "resourcechange"
            or e.get("sourceID") != player_id
        ):
            continue
        rtype = e.get("resourceChangeType")
        raw_waste = e.get("waste")
        if rtype is None or not isinstance(raw_waste, (int, float)):
            continue
        rtype = int(rtype)
        oracle_counts[rtype] = oracle_counts.get(rtype, 0) + 1
        oracle_waste[rtype] = oracle_waste.get(rtype, 0.0) + float(raw_waste)
    return {t: (oracle_counts[t], oracle_waste[t]) for t in oracle_counts}


def test_resource_replay_oracle_from_raw_json_matches_parse_resource_type_counts() -> None:
    resource_cassettes = _resource_cassettes()
    assert len(resource_cassettes) == 26, "the recorded cassette set changed size"

    checked = 0
    total_oracle_types = 0
    for cassette in resource_cassettes:
        events = (
            cassette["response_json"]
            .get("data", {})
            .get("reportData", {})
            .get("report", {})
            .get("events", {})
            .get("data", [])
        )
        source_ids = sorted(
            {e["sourceID"] for e in events if isinstance(e, dict) and "sourceID" in e}
        )
        for player_id in source_ids:
            oracle = _oracle_resource_counts(events, player_id)
            result, _incomplete = parse_resource_type_counts(events, player_id)
            assert result == oracle, (cassette["request_payload"]["variables"], player_id)
            total_oracle_types += len(oracle)
            checked += 1
    assert checked > 0
    assert total_oracle_types > 0, "no real resourcechange evidence found in the corpus"


# -- derived error-on-second-page: real page 1, a synthetic failing page 2 -----


def test_derived_second_page_failure_preserves_the_real_first_page_counts() -> None:
    """Uses a real recorded resource-events page as page 1 (its own
    `nextPageTimestamp`, whatever it was, forced non-None so pagination
    continues), then fails page 2 — mirroring exactly what
    `ingest/performance_fetch.py::_paginate_events` does on a live API
    error, without needing network access. The page-1 events (real,
    unmodified) must still be visible in the PARTIAL result — a paginated
    failure must not discard already-collected evidence.
    """
    from botgitgud.errors import ApiError
    from botgitgud.ingest.performance_fetch import fetch_resource_waste

    cassette = _resource_cassettes()[0]
    page_1_events = (
        cassette["response_json"]
        .get("data", {})
        .get("reportData", {})
        .get("report", {})
        .get("events", {})
        .get("data", [])
    )
    assert page_1_events, "the chosen cassette has no events to preserve"
    player_id = page_1_events[0]["sourceID"]
    oracle = _oracle_resource_counts(page_1_events, player_id)
    assert oracle, "the chosen player has no measurable resourcechange events on page 1"

    calls = {"n": 0}
    next_page_ms = 500000.0  # strictly inside (0, end_time_ms) so page 2 is requested

    def query_fn(*_args: object, **_kwargs: object) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "data": {
                    "reportData": {
                        "report": {
                            "events": {"data": page_1_events, "nextPageTimestamp": next_page_ms}
                        }
                    }
                }
            }
        raise ApiError("simulated page-2 failure")

    waste_by_label, provenance = fetch_resource_waste(
        query_fn,
        report_code="REPLAY",
        fight_id=1,
        player_id=player_id,
        start_time_ms=0.0,
        end_time_ms=999999.0,
    )
    assert calls["n"] == 2, "page 2 must actually have been requested"
    assert provenance.collection.status is CollectionStatus.PARTIAL
    assert provenance.collection.reasons == ("STREAM_PARTIAL", "API_ERROR")
    # The content proof: the retained subtotal is EXACTLY the independent
    # oracle computed from the real page-1 JSON, not merely "some positive
    # count" — a parser that dropped or corrupted page-1 values would fail
    # this even though it still reports PARTIAL/nonzero-count correctly.
    assert provenance.by_type == oracle
    assert provenance.player_event_count == sum(count for count, _waste in oracle.values())
    assert waste_by_label
