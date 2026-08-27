"""EB.4 — the cheap half of a benchmark build: fetching ONLY what
`SetupProfile` needs (talents, trinkets, setIDs, stats), never the full
`PlayerLog` pipeline (damage/cast/buff/debuff/resource events).

`QUERY_PLAYER_SETUP_ONLY` (wcl/queries.py) is fight-wide — one query returns
`playerDetails`/`combatantInfo` for every player in the fight, same shape as
`QUERY_PLAYER_META`'s own `table(dataType: Summary)`. This module's job is
just to fetch that raw `playerDetails` dict; extraction is entirely reused
from `ingest/wcl_parsing.py` (`find_player_in_details`/`extract_setup_profile`)
— never re-implemented here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

from botgitgud.errors import ApiError
from botgitgud.wcl.queries import QUERY_PLAYER_SETUP_ONLY

log = structlog.get_logger(__name__)

QueryFn = Callable[..., dict[str, Any]]


def fetch_fight_player_details(
    query_fn: QueryFn, *, report_code: str, fight_id: int
) -> dict[str, Any] | None:
    """One query, fight-wide: the raw `playerDetails` object
    (`{dps: [...], healers: [...], tanks: [...]}`, each entry carrying its
    own `combatantInfo`) for every player in this fight — the same object
    `LogFetcher._fetch_from_api` extracts from `QUERY_PLAYER_META` at
    log_fetcher.py's `report.get("table", {}).get("data", {}).get("playerDetails", {})`,
    reproduced verbatim here since `QUERY_PLAYER_SETUP_ONLY`'s response has
    the identical shape for that one field.

    `None` is a best-effort degrade (bad report code, private report, WCL
    outage) — same contract as `ingest/fight_rankings.py`'s
    `fetch_fight_rankings`, which this function is meant to be called
    alongside for a benchmark build.
    """
    try:
        res_json = query_fn(
            QUERY_PLAYER_SETUP_ONLY,
            {"code": report_code, "fightIDs": [fight_id]},
            op_name="fetch_player_setup_only",
        )
    except ApiError as e:
        log.warning(
            "benchmark_fetch.setup_query_failed",
            report_code=report_code,
            fight_id=fight_id,
            error=str(e),
        )
        return None

    report = res_json.get("data", {}).get("reportData", {}).get("report", {})
    if not isinstance(report, dict):
        return None
    summary_data = report.get("table", {})
    if not isinstance(summary_data, dict):
        return None
    player_details = summary_data.get("data", {})
    if not isinstance(player_details, dict):
        return None
    player_details = player_details.get("playerDetails", {})
    return player_details if isinstance(player_details, dict) else None
