"""T1.6 — cohort candidate discovery via characterRankings, replacing
bot.py's fetch_top_logs_for_cds. Resolves docs/desvios.md D-9 (that task
predicted this exact move once the Fase 1 ingest/ package existed).

The raw per-reference-player timeline fetch that used to live inside
fetch_top_logs_for_cds is retired here in favor of LogFetcher (T1.4),
which does the same job with typed models, Store-backed caching, and
proper exceptions instead of a silent None. fetch_cohort_logs is the thin
adapter between a page of ranking candidates and LogFetcher.fetch_many().
"""

from __future__ import annotations

import structlog

from botgitgud.analysis.cohort import (
    COHORT_MAX,
    COHORT_MIN_HARD,
    MAX_RANKING_PAGES,
    SANITY_BAND_PCT,
    classify_cohort_size,
    within_sanity_band,
)
from botgitgud.domain.models import PlayerLog, RankingCandidate
from botgitgud.errors import ApiError, DataError, InsufficientCohort, RateLimitBudgetExceeded
from botgitgud.ingest.log_fetcher import LogFetcher, LogRequest
from botgitgud.wcl.client import WclClient
from botgitgud.wcl.queries import QUERY_RANKINGS_PAGE, QUERY_ZONE_PARTITIONS

log = structlog.get_logger(__name__)


def get_current_partition(client: WclClient, encounter_id: int) -> int:
    """T1.7 (docs/schema_confirmado.md §11): resolves the live `default`
    partition for the zone this encounter belongs to. Never hardcode a
    partition number — it changes as new content patches ship (observed
    live: the fixture zone's default moved from partition 4 to 3 between
    T0.1 and T1.7).
    """
    res_json = client.query(
        QUERY_ZONE_PARTITIONS, {"encounterID": encounter_id}, op_name="fetch_zone_partitions"
    )
    partitions = (
        res_json.get("data", {})
        .get("worldData", {})
        .get("encounter", {})
        .get("zone", {})
        .get("partitions", [])
    )
    for p in partitions:
        if p.get("default"):
            return p["id"]
    msg = f"nenhuma partition default encontrada para o encontro {encounter_id}"
    raise DataError(msg)


def fetch_ranking_candidates(
    client: WclClient,
    *,
    encounter_id: int,
    class_name: str,
    spec_name: str,
    partition: int,
    target_duration_s: float | None,
) -> list[RankingCandidate]:
    """Pages through characterRankings for the given (current) partition.

    When `target_duration_s` is given, keeps only entries within the
    sanity band (T0.8: ±35%, an adjustment covariate, not a hard "similar
    kill" filter) — the normal per-analysis path. When None, keeps every
    candidate found (up to COHORT_MAX) regardless of duration — used by
    T1.7's batch `build-cohort` to discover every duration bucket present
    in the live pool in one pass, instead of one sanity-band-filtered
    fetch per bucket.

    Raises InsufficientCohort if too few candidates survive overall.
    """
    clean_spec = (
        spec_name.replace(class_name, "").strip() if class_name in spec_name else spec_name.strip()
    )

    candidates: list[RankingCandidate] = []
    page = 1
    while page <= MAX_RANKING_PAGES and len(candidates) < COHORT_MAX:
        variables = {
            "encounterID": encounter_id,
            "className": class_name.strip(),
            "specName": clean_spec,
            "page": page,
            "partition": partition,
        }
        try:
            res_json = client.query(QUERY_RANKINGS_PAGE, variables, op_name="fetch_rankings_page")
        except RateLimitBudgetExceeded:
            # Global condition, not a per-page defect — never swallowed as
            # "this page failed" (T1.7, same fix as LogFetcher.fetch_many).
            raise
        except ApiError as e:
            log.warning("rankings.page_failed", page=page, error=str(e))
            break

        rankings_data = (
            res_json.get("data", {})
            .get("worldData", {})
            .get("encounter", {})
            .get("characterRankings", {})
        )
        rankings_list = rankings_data.get("rankings", [])
        if not rankings_list:
            break

        for r in rankings_list:
            dur_s = r.get("duration", 0) / 1000.0
            if target_duration_s is not None and not within_sanity_band(dur_s, target_duration_s):
                continue
            rep = r.get("report", {})
            if rep.get("code") and rep.get("fightID") is not None:
                candidates.append(
                    RankingCandidate(
                        report_code=rep["code"],
                        fight_id=rep["fightID"],
                        player_name=r["name"],
                        duration_s=dur_s,
                    )
                )

        if not rankings_data.get("hasMorePages", False):
            break
        page += 1

    if classify_cohort_size(len(candidates)) == "insufficient":
        msg = f"apenas {len(candidates)} logs de referência encontrados (mínimo: {COHORT_MIN_HARD})"
        if target_duration_s is not None:
            msg = (
                f"apenas {len(candidates)} logs de referência dentro da banda de "
                f"±{int(SANITY_BAND_PCT * 100)}% de duração (mínimo: {COHORT_MIN_HARD})"
            )
        raise InsufficientCohort(msg, n_members=len(candidates), minimum_required=COHORT_MIN_HARD)

    return candidates[:COHORT_MAX]


def fetch_cohort_logs(
    fetcher: LogFetcher, candidates: list[RankingCandidate], *, max_workers: int
) -> list[PlayerLog]:
    refs = [LogRequest(c.report_code, c.fight_id, c.player_name) for c in candidates]
    return fetcher.fetch_many(refs, max_workers=max_workers)
