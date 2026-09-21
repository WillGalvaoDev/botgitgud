"""T2.1 split of LogFetcher's best-effort auxiliary fetches (percentile,
buffs) out of ingest/log_fetcher.py, to keep it under the 300-line limit
(docs/implementacao.md T1.6's rule, still enforced repo-wide). Both
functions take `query_fn` (LogFetcher._query, bound) instead of being
methods, so they don't need LogFetcher's internals — just something that
issues a GraphQL query and counts it.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any

import structlog

from botgitgud.domain.external_buffs import AUGMENTATION_BUFF_IDS, EXTERNAL_BUFF_IDS
from botgitgud.domain.models import (
    AuraDetail,
    CollectionProvenance,
    CollectionStatus,
    PhaseInterval,
    PhaseKey,
)
from botgitgud.domain.spells import SpellCatalog
from botgitgud.errors import ApiError
from botgitgud.ingest.event_validation import valid_event
from botgitgud.ingest.performance_parsing import parse_aura_uptimes
from botgitgud.ingest.wcl_parsing import (
    find_matching_rank_percent,
    parse_aura_ids,
    parse_cast_events,
    parse_cast_events_by_phase,
)
from botgitgud.wcl.queries import (
    QUERY_PLAYER_BUFFS,
    QUERY_PLAYER_DEBUFFS,
    QUERY_PLAYER_EVENTS,
    QUERY_PLAYER_PERCENTILE,
)

log = structlog.get_logger(__name__)

QueryFn = Callable[..., dict[str, Any]]


def fetch_percentile(
    query_fn: QueryFn,
    *,
    report_code: str,
    fight_id: int,
    player: str,
    server: str | None,
    region: str | None,
    encounter_id: int,
    difficulty: int | None,
) -> float | None:
    """Best-effort: None (never fabricated) on any missing input or API
    failure — see find_matching_rank_percent for the real source.
    """
    if not server or not region or not difficulty:
        return None

    server_slug = server.lower().replace(" ", "-")
    try:
        res_json = query_fn(
            QUERY_PLAYER_PERCENTILE,
            {
                "name": player,
                "serverSlug": server_slug,
                "serverRegion": region,
                "encounterID": encounter_id,
                "difficulty": difficulty,
            },
            op_name="fetch_player_percentile",
        )
    except ApiError as e:
        log.warning("log_fetcher.percentile_failed", error=str(e))
        return None

    character = res_json.get("data", {}).get("characterData", {}).get("character")
    return find_matching_rank_percent(character, report_code, fight_id)


def fetch_buffs_and_debuffs(
    query_fn: QueryFn,
    *,
    report_code: str,
    fight_id: int,
    player_id: int,
    catalog: SpellCatalog,
) -> tuple[bool, frozenset[int], dict[int, float], dict[int, AuraDetail]]:
    """T2.1 (has_augmentation/external_buffs) + T3.1 (uptimes): one query
    each to the Buffs and Debuffs tables — a single Buffs fetch serves
    both purposes rather than querying it twice. (False, frozenset(), {})
    contribution on any failure — buff/debuff data is best-effort and
    never blocks the whole log fetch (has_augmentation/external_buffs
    already degrade gracefully in the match cascade; a missing uptime is
    just absent from the report, not fabricated as 0).
    """
    has_augmentation = False
    external_buffs: frozenset[int] = frozenset()
    uptimes: dict[int, float] = {}
    aura_details: dict[int, AuraDetail] = {}

    try:
        res_json = query_fn(
            QUERY_PLAYER_BUFFS,
            {"code": report_code, "fightIDs": [fight_id], "sourceID": player_id},
            op_name="fetch_player_buffs",
        )
        buffs_data = (
            res_json.get("data", {})
            .get("reportData", {})
            .get("report", {})
            .get("table", {})
            .get("data", {})
        )
        aura_ids = parse_aura_ids(buffs_data)
        has_augmentation = bool(aura_ids & AUGMENTATION_BUFF_IDS)
        external_buffs = aura_ids & EXTERNAL_BUFF_IDS
        for aura in parse_aura_uptimes(buffs_data):
            catalog.learn(aura.spell_id, aura.name, "wcl")
            uptimes[aura.spell_id] = aura.uptime_frac
            aura_details[aura.spell_id] = AuraDetail(aura.total_uses, aura.bands)
    except ApiError as e:
        log.warning("log_fetcher.buffs_failed", error=str(e))

    try:
        res_json = query_fn(
            QUERY_PLAYER_DEBUFFS,
            {"code": report_code, "fightIDs": [fight_id], "sourceID": player_id},
            op_name="fetch_player_debuffs",
        )
        debuffs_data = (
            res_json.get("data", {})
            .get("reportData", {})
            .get("report", {})
            .get("table", {})
            .get("data", {})
        )
        for aura in parse_aura_uptimes(debuffs_data):
            catalog.learn(aura.spell_id, aura.name, "wcl")
            uptimes[aura.spell_id] = aura.uptime_frac
            aura_details[aura.spell_id] = AuraDetail(aura.total_uses, aura.bands)
    except ApiError as e:
        log.warning("log_fetcher.debuffs_failed", error=str(e))

    return has_augmentation, external_buffs, uptimes, aura_details


def fetch_cast_timelines(
    query_fn: QueryFn,
    *,
    report_code: str,
    fight_id: int,
    player_id: int,
    start_time_ms: float,
    end_time_ms: float,
    intervals: Sequence[PhaseInterval],
    include_provenance: bool = False,
) -> tuple:
    """T1.4/T2.4: pages through GetPlayerEvents once, building BOTH the
    flat (relative-to-fight-start) and phase-keyed (relative-to-interval-
    start) timelines from the same events — never fetched twice.
    """
    flat_by_id: dict[int, list[float]] = {}
    phase_by_id: dict[int, dict[PhaseKey, list[float]]] = {}
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
        if include_provenance:
            return (
                {},
                {},
                CollectionProvenance(
                    CollectionStatus.UNKNOWN,
                    ("INVALID_INTERVAL",),
                    start_time_ms,
                    end_time_ms,
                ),
            )
        return {}, {}
    while current_start < end_time_ms:
        try:
            ev_res_json = query_fn(
                QUERY_PLAYER_EVENTS,
                {
                    "code": report_code,
                    "fightIDs": [fight_id],
                    "startTime": current_start,
                    "endTime": end_time_ms,
                },
                op_name="fetch_player_events",
            )
        except ApiError:
            status = CollectionStatus.PARTIAL
            reasons.append("API_ERROR")
            break

        if not isinstance(ev_res_json, dict) or not isinstance(ev_res_json.get("data"), dict):
            status, reasons = CollectionStatus.PARTIAL, ["INVALID_RESPONSE"]
            break
        report_data = ev_res_json["data"].get("reportData")
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
        raw_events = ev_data.get("data")
        if not isinstance(raw_events, list):
            status, reasons = CollectionStatus.PARTIAL, ["INVALID_RESPONSE"]
            break
        if any(
            not valid_event(event, start_time_ms=current_start, end_time_ms=end_time_ms)
            for event in raw_events
        ):
            status, reasons = CollectionStatus.PARTIAL, ["INVALID_EVENT"]
            break

        page_flat = parse_cast_events(raw_events, player_id, start_time_ms)
        for spell_id, times in page_flat.items():
            flat_by_id.setdefault(spell_id, []).extend(times)

        page_phase = parse_cast_events_by_phase(raw_events, player_id, intervals)
        for spell_id, by_key in page_phase.items():
            dest = phase_by_id.setdefault(spell_id, {})
            for key, times in by_key.items():
                dest.setdefault(key, []).extend(times)

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

    flat = {sid: tuple(times) for sid, times in flat_by_id.items()}
    phased = {
        sid: {key: tuple(times) for key, times in by_key.items()}
        for sid, by_key in phase_by_id.items()
    }
    result = (flat, phased)
    return (
        (*result, CollectionProvenance(status, tuple(reasons), start_time_ms, end_time_ms))
        if include_provenance
        else result
    )
