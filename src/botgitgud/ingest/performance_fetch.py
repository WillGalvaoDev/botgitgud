"""T3.1 — paginated fetches for the two event-heavy performance features
(pet-aware damage-by-ability, resource waste). Split out of
ingest/log_fetcher_aux.py to keep every file under the 300-line limit
(docs/implementacao.md T1.6) — mirrors that module's own
`fetch_cast_timelines` pagination pattern (page via `nextPageTimestamp`
until it stalls or reaches `end_time_ms`).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any

import structlog

from botgitgud.domain.models import CollectionProvenance, CollectionStatus
from botgitgud.domain.resource_types import resource_type_label
from botgitgud.errors import ApiError
from botgitgud.ingest.damage_aggregation import (
    RawDamageEvent,
    aggregate_damage_by_ability,
    parse_damage_events,
    scope_total,
    support_subtracted_total,
)
from botgitgud.ingest.event_validation import valid_event
from botgitgud.ingest.performance_parsing import parse_resource_waste
from botgitgud.wcl.queries import QUERY_PLAYER_DAMAGE_EVENTS, QUERY_PLAYER_RESOURCE_EVENTS

log = structlog.get_logger(__name__)

QueryFn = Callable[..., dict[str, Any]]


def _paginate_events(
    query_fn: QueryFn,
    query: str,
    *,
    report_code: str,
    fight_id: int,
    start_time_ms: float,
    end_time_ms: float,
    op_name: str,
) -> tuple[list[dict[str, Any]], CollectionProvenance]:
    all_events: list[dict[str, Any]] = []
    current_start = start_time_ms
    status = CollectionStatus.COMPLETE
    reasons: list[str] = []
    if not (
        not isinstance(start_time_ms, bool)
        and not isinstance(end_time_ms, bool)
        and math.isfinite(start_time_ms)
        and math.isfinite(end_time_ms)
        and 0 <= start_time_ms < end_time_ms
    ):
        return all_events, CollectionProvenance(
            CollectionStatus.UNKNOWN, ("INVALID_INTERVAL",), start_time_ms, end_time_ms
        )
    while current_start < end_time_ms:
        try:
            res_json = query_fn(
                query,
                {
                    "code": report_code,
                    "fightIDs": [fight_id],
                    "startTime": current_start,
                    "endTime": end_time_ms,
                },
                op_name=op_name,
            )
        except ApiError as e:
            log.warning("performance_fetch.page_failed", op_name=op_name, error=str(e))
            status = CollectionStatus.PARTIAL
            reasons.append("API_ERROR")
            break

        if not isinstance(res_json, dict):
            status, reasons = CollectionStatus.PARTIAL, ["INVALID_RESPONSE"]
            break
        data = res_json.get("data")
        if not isinstance(data, dict):
            status, reasons = CollectionStatus.PARTIAL, ["INVALID_RESPONSE"]
            break
        report_data = data.get("reportData")
        if not isinstance(report_data, dict):
            status, reasons = CollectionStatus.PARTIAL, ["INVALID_RESPONSE"]
            break
        report = report_data.get("report")
        if not isinstance(report, dict):
            status, reasons = CollectionStatus.PARTIAL, ["INVALID_RESPONSE"]
            break
        ev_data = report.get("events")
        if not isinstance(ev_data, dict) or (
            "data" not in ev_data or "nextPageTimestamp" not in ev_data
        ):
            status = CollectionStatus.PARTIAL
            reasons.append("INVALID_RESPONSE")
            break
        page_events = ev_data.get("data")
        if not isinstance(page_events, list):
            status, reasons = CollectionStatus.PARTIAL, ["INVALID_RESPONSE"]
            break
        if any(
            not valid_event(event, start_time_ms=current_start, end_time_ms=end_time_ms)
            for event in page_events
        ):
            status, reasons = CollectionStatus.PARTIAL, ["INVALID_EVENT"]
            break
        all_events.extend(page_events)

        next_page = ev_data.get("nextPageTimestamp")
        if next_page is not None and (
            isinstance(next_page, bool)
            or not isinstance(next_page, (int, float))
            or not math.isfinite(float(next_page))
        ):
            status = CollectionStatus.PARTIAL
            reasons.append("INVALID_CURSOR")
            break
        if next_page is not None and next_page <= current_start:
            status = CollectionStatus.PARTIAL
            reasons.append("CURSOR_NO_PROGRESS")
            break
        if next_page is None or next_page >= end_time_ms:
            break
        current_start = next_page
    return all_events, CollectionProvenance(status, tuple(reasons), start_time_ms, end_time_ms)


def fetch_damage_and_targets(
    query_fn: QueryFn,
    *,
    report_code: str,
    fight_id: int,
    start_time_ms: float,
    end_time_ms: float,
    source_ids: frozenset[int],
    cast_counts: Mapping[int, int],
) -> tuple[dict, dict[int, float]]:
    """Pages `events(dataType: DamageDone)` once for the whole fight (no
    server-side sourceID filter — a player's own source_id is only one of
    several, alongside every pet's, docs/schema_confirmado.md §5), then
    aggregates client-side.
    """
    raw_events_json, _ = _paginate_events(
        query_fn,
        QUERY_PLAYER_DAMAGE_EVENTS,
        report_code=report_code,
        fight_id=fight_id,
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
        op_name="fetch_player_damage_events",
    )
    raw_events: list[RawDamageEvent] = parse_damage_events(raw_events_json, source_ids)
    return aggregate_damage_by_ability(raw_events, cast_counts)


def fetch_scoped_damage_and_targets(
    query_fn: QueryFn,
    *,
    report_code: str,
    fight_id: int,
    start_time_ms: float,
    end_time_ms: float,
    player_id: int,
    source_ids: frozenset[int],
    target_ids: frozenset[int],
    pet_owner_by_actor: Mapping[int, int],
    cast_counts: Mapping[int, int],
    include_provenance: bool = False,
) -> tuple:
    """Fetch raw events once and return V1 decomposition and total terms."""
    events, provenance = _paginate_events(
        query_fn,
        QUERY_PLAYER_DAMAGE_EVENTS,
        report_code=report_code,
        fight_id=fight_id,
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
        op_name="fetch_player_damage_events",
    )
    unscoped_events = parse_damage_events(events, source_ids)
    unscoped_own = sum(event.amount for event in unscoped_events)
    scoped_events = parse_damage_events(events, source_ids, target_ids)
    damage_by_ability, avg_targets = aggregate_damage_by_ability(scoped_events, cast_counts)
    support_subtracted = support_subtracted_total(events, player_id, pet_owner_by_actor, target_ids)
    damaged_target_ids = frozenset(
        int(event["targetID"])
        for event in events
        if event.get("type") == "damage" and isinstance(event.get("targetID"), int)
    )
    result = (
        damage_by_ability,
        avg_targets,
        support_subtracted,
        scope_total(damage_by_ability, support_subtracted),
        unscoped_own,
        damaged_target_ids,
    )
    if include_provenance:
        from botgitgud.ingest.damage_aggregation import build_event_mix

        return (
            *result,
            provenance,
            build_event_mix(scoped_events, player_id, frozenset(pet_owner_by_actor)),
        )
    return result


def fetch_resource_waste(
    query_fn: QueryFn,
    *,
    report_code: str,
    fight_id: int,
    player_id: int,
    start_time_ms: float,
    end_time_ms: float,
) -> dict[str, float]:
    """Pages `events(dataType: Resources)` for the whole fight, filters to
    this player's own resourcechange events client-side (mirrors
    fetch_cast_timelines' own pattern), and labels each
    `resourceChangeType` via domain/resource_types.py.
    """
    events, _ = _paginate_events(
        query_fn,
        QUERY_PLAYER_RESOURCE_EVENTS,
        report_code=report_code,
        fight_id=fight_id,
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
        op_name="fetch_player_resource_events",
    )
    waste_by_type = parse_resource_waste(events, player_id)
    return {resource_type_label(t): v for t, v in waste_by_type.items()}
