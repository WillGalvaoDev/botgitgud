"""T3.1 — paginated fetches for the two event-heavy performance features
(pet-aware damage-by-ability, resource waste). Split out of
ingest/log_fetcher_aux.py to keep every file under the 300-line limit
(docs/implementacao.md T1.6) — mirrors that module's own
`fetch_cast_timelines` pagination pattern (page via `nextPageTimestamp`
until it stalls or reaches `end_time_ms`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import structlog

from botgitgud.domain.resource_types import resource_type_label
from botgitgud.errors import ApiError
from botgitgud.ingest.damage_aggregation import (
    RawDamageEvent,
    aggregate_damage_by_ability,
    parse_damage_events,
)
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
) -> list[dict[str, Any]]:
    all_events: list[dict[str, Any]] = []
    current_start = start_time_ms
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
            break

        ev_data = res_json.get("data", {}).get("reportData", {}).get("report", {}).get("events", {})
        all_events.extend(ev_data.get("data", []))

        next_page = ev_data.get("nextPageTimestamp")
        if not next_page or next_page <= current_start or next_page >= end_time_ms:
            break
        current_start = next_page
    return all_events


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
    raw_events_json = _paginate_events(
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
    events = _paginate_events(
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
