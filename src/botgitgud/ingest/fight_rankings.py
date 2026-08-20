"""T-DG.1 (docs/fase4-data-acquisition-plan.md §11) — query + parser for
reportData.report.rankings, the bulk per-fight source of rankPercent and
partition discovered during the Fase 4 data-gate investigation
(docs/schema_confirmado.md §13.3). One query costs 2.0 points regardless
of player count and returned 100% rankPercent coverage in the live probe
(169/169), versus ~40% coverage at 1.0 point PER PLAYER for the percentile
source ingest/wcl_parsing.py's find_matching_rank_percent already uses.

Only the `dps` role group is parsed — the tool's entire scope is DPS specs
(domain/specs.py, T0.9); tanks/healers/Augmentation are rejected downstream
and are never useful as training observations. `rankPercent` here is an
integer (measured: 48, 88, 94, ...), coarser than the float
`characterData...encounterRankings` already used elsewhere returns — a
documented precision loss (docs/schema_confirmado.md §13.3), not a bug.

T-DG.4 adds `parse_report_rankings_all`/`QUERY_REPORT_RANKINGS_ALL_FIGHTS`:
report.rankings with `fightIDs` omitted returns every ranked fight of a
report in one call, not just one — the fact that lets Estágio B triage a
report without knowing any fight_id in advance, spec/encounter-agnostic by
construction (the user-approved plan explicitly forbids fixing a target
before the census).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog

from botgitgud.errors import ApiError
from botgitgud.wcl.queries import QUERY_REPORT_RANKINGS

log = structlog.get_logger(__name__)

QueryFn = Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class DpsRanking:
    player_name: str
    server_name: str | None
    server_region: str | None
    class_name: str
    spec_name: str
    amount: float | None  # DPS
    rank_percent: float | None  # integer-valued in practice; see module docstring
    bracket_data: float | None
    total_parses: int | None


@dataclass(frozen=True, slots=True)
class FightRankings:
    fight_id: int
    partition: int | None
    encounter_id: int | None
    difficulty: int | None
    size: int | None
    kill: bool
    duration_s: float | None
    dps: tuple[DpsRanking, ...]


def _rankings_data_list(res_json: dict[str, Any]) -> list[Any] | None:
    rankings = res_json.get("data", {}).get("reportData", {}).get("report", {}).get("rankings")
    if not isinstance(rankings, dict):
        return None
    data = rankings.get("data")
    return data if isinstance(data, list) else None


def parse_report_rankings(res_json: dict[str, Any], *, fight_id: int) -> FightRankings | None:
    """None whenever the fight has no rankings entry (private report, a
    wipe not counted, or WCL simply lacking ranking data for it) or the
    response is malformed in any way — never fabricated, mirroring every
    other best-effort parser in ingest/ (e.g. find_matching_rank_percent).
    """
    data = _rankings_data_list(res_json)
    if not data:
        return None
    entry = data[0]
    if not isinstance(entry, dict):
        return None
    return _parse_fight_entry(entry, fight_id=fight_id)


def parse_report_rankings_all(res_json: dict[str, Any]) -> tuple[FightRankings, ...]:
    """T-DG.4: unlike parse_report_rankings (one already-known fight_id),
    parses EVERY fight entry the response contains — pairs with
    QUERY_REPORT_RANKINGS_ALL_FIGHTS (report.rankings with no fightIDs
    filter), which live-measurement showed returns every ranked fight of a
    report in one call (docs/schema_confirmado.md §13.3 update: 6/6 fights,
    same ~2.0 pts/fight as the single-fight form) — the fact that makes
    Estágio B's triage spec/encounter-agnostic, never needing to know a
    fight_id in advance. Entries missing a usable `fightID` are skipped,
    never fabricated.
    """
    data = _rankings_data_list(res_json)
    if not data:
        return ()
    results: list[FightRankings] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        fight_id = entry.get("fightID")
        if not isinstance(fight_id, int):
            continue
        results.append(_parse_fight_entry(entry, fight_id=fight_id))
    return tuple(results)


def _parse_fight_entry(entry: dict[str, Any], *, fight_id: int) -> FightRankings:
    roles = entry.get("roles")
    dps_group = roles.get("dps") if isinstance(roles, dict) else None
    characters = dps_group.get("characters") if isinstance(dps_group, dict) else None

    dps: list[DpsRanking] = []
    for c in characters or []:
        if not isinstance(c, dict):
            continue
        name, class_name, spec_name = c.get("name"), c.get("class"), c.get("spec")
        if not name or not class_name or not spec_name:
            continue
        raw_server = c.get("server")
        server = raw_server if isinstance(raw_server, dict) else {}
        dps.append(
            DpsRanking(
                player_name=name,
                server_name=server.get("name"),
                server_region=server.get("region"),
                class_name=class_name,
                spec_name=spec_name,
                amount=c.get("amount"),
                rank_percent=c.get("rankPercent"),
                bracket_data=c.get("bracketData"),
                total_parses=c.get("totalParses"),
            )
        )

    encounter = entry.get("encounter")
    encounter_id = encounter.get("id") if isinstance(encounter, dict) else None
    duration_ms = entry.get("duration")
    duration_s = duration_ms / 1000.0 if isinstance(duration_ms, int | float) else None

    return FightRankings(
        fight_id=fight_id,
        partition=entry.get("partition"),
        encounter_id=encounter_id,
        difficulty=entry.get("difficulty"),
        size=entry.get("size"),
        kill=bool(entry.get("kill")),
        duration_s=duration_s,
        dps=tuple(dps),
    )


def fetch_fight_rankings(
    query_fn: QueryFn, *, report_code: str, fight_id: int
) -> FightRankings | None:
    """Best-effort: an ApiError (matched WCL outage, bad report code, ...)
    degrades to None rather than raising — the same contract as
    ingest/log_fetcher_aux.py's fetch_percentile, which this function is
    meant to replace as the partition/rankPercent source.
    """
    try:
        res_json = query_fn(
            QUERY_REPORT_RANKINGS,
            {"code": report_code, "fightIDs": [fight_id]},
            op_name="fetch_report_rankings",
        )
    except ApiError as e:
        log.warning("fight_rankings.fetch_failed", error=str(e))
        return None
    return parse_report_rankings(res_json, fight_id=fight_id)


def fetch_partition(query_fn: QueryFn, *, report_code: str, fight_id: int) -> int | None:
    """T-DG.0: thin wrapper for callers (LogFetcher) that only need the
    partition, not the full per-player rankings — keeps that call site to
    one line.
    """
    fight_rankings = fetch_fight_rankings(query_fn, report_code=report_code, fight_id=fight_id)
    return fight_rankings.partition if fight_rankings is not None else None
