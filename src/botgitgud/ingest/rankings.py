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

from dataclasses import dataclass

import structlog

from botgitgud.analysis.cohort import (
    COHORT_MAX,
    COHORT_MIN_HARD,
    MAX_RANKING_PAGES,
    SANITY_BAND_PCT,
    classify_cohort_size,
    within_sanity_band,
)
from botgitgud.domain.models import PlayerLog
from botgitgud.errors import ApiError, InsufficientCohort
from botgitgud.ingest.log_fetcher import LogFetcher, LogRequest
from botgitgud.wcl.client import WclClient
from botgitgud.wcl.queries import QUERY_RANKINGS_PAGE

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RankingCandidate:
    report_code: str
    fight_id: int
    player_name: str
    duration_s: float


def fetch_ranking_candidates(
    client: WclClient,
    *,
    encounter_id: int,
    class_name: str,
    spec_name: str,
    target_duration_s: float,
) -> list[RankingCandidate]:
    """Pages through characterRankings, keeping only entries within the
    sanity band (T0.8: ±35%, an adjustment covariate, not a hard "similar
    kill" filter). Raises InsufficientCohort if too few survive.
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
        }
        try:
            res_json = client.query(QUERY_RANKINGS_PAGE, variables, op_name="fetch_rankings_page")
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
            if not within_sanity_band(dur_s, target_duration_s):
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
