"""T2.1 split of LogFetcher's best-effort auxiliary fetches (percentile,
buffs) out of ingest/log_fetcher.py, to keep it under the 300-line limit
(docs/implementacao.md T1.6's rule, still enforced repo-wide). Both
functions take `query_fn` (LogFetcher._query, bound) instead of being
methods, so they don't need LogFetcher's internals — just something that
issues a GraphQL query and counts it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

from botgitgud.domain.external_buffs import AUGMENTATION_BUFF_IDS, EXTERNAL_BUFF_IDS
from botgitgud.errors import ApiError
from botgitgud.ingest.wcl_parsing import find_matching_rank_percent, parse_aura_ids
from botgitgud.wcl.queries import QUERY_PLAYER_BUFFS, QUERY_PLAYER_PERCENTILE

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


def fetch_augmentation_and_external_buffs(
    query_fn: QueryFn, *, report_code: str, fight_id: int, player_id: int
) -> tuple[bool, frozenset[int]]:
    """T2.1: (False, frozenset()) on any failure — buff data is best-effort
    and never blocks the whole log fetch (has_augmentation/external_buffs
    are covariates that already degrade gracefully in the match cascade).
    """
    try:
        res_json = query_fn(
            QUERY_PLAYER_BUFFS,
            {"code": report_code, "fightIDs": [fight_id], "sourceID": player_id},
            op_name="fetch_player_buffs",
        )
    except ApiError as e:
        log.warning("log_fetcher.buffs_failed", error=str(e))
        return False, frozenset()

    report = res_json.get("data", {}).get("reportData", {}).get("report", {})
    buffs_data = report.get("table", {}).get("data", {})
    aura_ids = parse_aura_ids(buffs_data)
    has_augmentation = bool(aura_ids & AUGMENTATION_BUFF_IDS)
    external_buffs = aura_ids & EXTERNAL_BUFF_IDS
    return has_augmentation, external_buffs
