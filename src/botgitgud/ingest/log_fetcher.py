"""T1.4 — cached log ingestion, the fix for achado 4.2 (the project's
biggest architectural flaw): Fase 0's fetch_player_timeline_data() hit the
live API on every call, even for reference players whose logs never
change. LogFetcher checks the Store first; a finished fight's log is
immutable forever (Store's own principle, T1.3), so a cache hit costs zero
API points. Raises PlayerNotFound/FightNotFound instead of Fase 0's legacy
None-returning pattern — fetch() always returns a PlayerLog or raises.
JSON parsing itself lives in ingest/wcl_parsing.py (T1.6 split).

Fields Fase 0 never resolved (talent_hash, tier_pieces, external_buffs,
damage_by_ability, uptimes, resource_waste, active_time_pct, deaths,
partition) default on PlayerLog/PlayerBuild/FightRef — T2.1/T2.2/T3.1/T1.7.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import structlog

from botgitgud.domain.models import FightRef, PlayerBuild, PlayerLog
from botgitgud.domain.spells import SpellCatalog
from botgitgud.errors import ApiError, FightNotFound, PlayerNotFound
from botgitgud.ingest.store import Store
from botgitgud.ingest.wcl_parsing import (
    extract_damage_total,
    find_matching_rank_percent,
    find_player_in_details,
    learn_spells_from_casts_table,
    parse_cast_events,
)
from botgitgud.wcl.client import WclClient
from botgitgud.wcl.queries import QUERY_PLAYER_EVENTS, QUERY_PLAYER_META, QUERY_PLAYER_PERCENTILE

log = structlog.get_logger(__name__)

# Measured live against the real API (docs/schema_confirmado.md §2/§11):
# every query costs ~2.0 points regardless of complexity. Used to estimate
# api_points_spent from a counted number of queries, since WclClient's own
# points_remaining is a periodically-cached snapshot (T0.3), not a live
# running counter, and can't reliably produce an accurate before/after delta
# for a batch that completes faster than its cache TTL.
_POINTS_PER_QUERY = 2.0


@dataclass(frozen=True, slots=True)
class LogRequest:
    report_code: str
    fight_id: int
    player: str


class LogFetcher:
    def __init__(self, client: WclClient, store: Store, catalog: SpellCatalog) -> None:
        self._client = client
        self._store = store
        self._catalog = catalog
        self._query_count_lock = threading.Lock()
        self._query_count = 0

    def fetch(
        self, report_code: str, fight_id: int, player: str, *, force: bool = False
    ) -> PlayerLog:
        if not force:
            cached = self._store.read_log(report_code, fight_id, player)
            if cached is not None:
                return cached

        player_log = self._fetch_from_api(report_code, fight_id, player)
        self._store.write_log(player_log)
        return player_log

    def fetch_many(self, refs: Sequence[LogRequest], *, max_workers: int) -> list[PlayerLog]:
        """Best-effort: a ref that fails (PlayerNotFound/FightNotFound/
        ApiError) is logged and skipped rather than aborting the whole
        batch — a reference-cohort fetch expects some fraction of ranked
        logs to 404 or error out, unlike fetch()'s single-ref call, which
        still raises. Returned list preserves the relative order of `refs`
        but may be shorter than it when some refs failed.
        """
        start = time.monotonic()
        with self._query_count_lock:
            self._query_count = 0

        results: dict[int, PlayerLog] = {}
        cache_hits = 0
        to_fetch: list[tuple[int, LogRequest]] = []

        # Cache lookups first, sequentially — cheap, and avoids spinning up
        # worker threads for refs that need no network call at all.
        for i, ref in enumerate(refs):
            cached = self._store.read_log(ref.report_code, ref.fight_id, ref.player)
            if cached is not None:
                results[i] = cached
                cache_hits += 1
            else:
                to_fetch.append((i, ref))

        fetched_logs: list[PlayerLog] = []
        failures = 0
        if to_fetch:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        self._fetch_from_api, ref.report_code, ref.fight_id, ref.player
                    ): i
                    for i, ref in to_fetch
                }
                for future in as_completed(futures):
                    i = futures[future]
                    try:
                        player_log = future.result()
                    except (PlayerNotFound, FightNotFound, ApiError) as e:
                        failures += 1
                        log.warning("log_fetcher.fetch_many_ref_failed", index=i, error=str(e))
                        continue
                    results[i] = player_log
                    fetched_logs.append(player_log)

        # T1.4's own rule: no disk writes inside worker threads — persist
        # here, on the calling thread, after every future has resolved.
        for player_log in fetched_logs:
            self._store.write_log(player_log)

        with self._query_count_lock:
            queries_made = self._query_count
        wall_time_s = time.monotonic() - start

        log.info(
            "log_fetcher.fetch_many",
            cache_hits=cache_hits,
            cache_misses=len(fetched_logs),
            failures=failures,
            api_points_spent=queries_made * _POINTS_PER_QUERY,
            wall_time_s=round(wall_time_s, 3),
        )

        return [results[i] for i in range(len(refs)) if i in results]

    # -- internal ---------------------------------------------------------------

    def _query(self, query: str, variables: dict[str, object], *, op_name: str) -> dict:
        with self._query_count_lock:
            self._query_count += 1
        return self._client.query(query, variables, op_name=op_name)

    def _fetch_from_api(self, report_code: str, fight_id: int, player: str) -> PlayerLog:
        res_json = self._query(
            QUERY_PLAYER_META,
            {"code": report_code, "fightIDs": [fight_id]},
            op_name="fetch_player_meta",
        )
        report = res_json.get("data", {}).get("reportData", {}).get("report", {})
        fights = report.get("fights", [])
        if not fights:
            msg = f"fight {fight_id} não encontrado no report {report_code}"
            raise FightNotFound(msg)

        raw_fight = fights[0]
        start_time_ms = raw_fight["startTime"]
        end_time_ms = raw_fight["endTime"]
        duration_s = (end_time_ms - start_time_ms) / 1000.0

        summary_data = report.get("table", {}).get("data", {})
        player_details = summary_data.get("playerDetails", {})
        match = find_player_in_details(player_details, player)
        if match is None:
            msg = f"jogador '{player}' não encontrado no fight {fight_id} do report {report_code}"
            raise PlayerNotFound(msg)

        damage_total = extract_damage_total(summary_data.get("damageDone", []), match.player_id)

        casts_entries = report.get("castsTable", {}).get("data", {}).get("entries", [])
        learn_spells_from_casts_table(casts_entries, match.player_id, self._catalog)

        cast_timeline = self._fetch_cast_timeline(
            report_code, fight_id, match.player_id, start_time_ms, end_time_ms
        )
        dps = (damage_total / duration_s) if (damage_total and duration_s > 0) else None

        percentile = self._fetch_percentile(
            report_code=report_code,
            fight_id=fight_id,
            player=player,
            server=match.server,
            region=match.region,
            encounter_id=raw_fight["encounterID"],
            difficulty=raw_fight.get("difficulty"),
        )

        fight = FightRef(
            report_code=report_code,
            fight_id=fight_id,
            encounter_id=raw_fight["encounterID"],
            boss_name=raw_fight["name"],
            difficulty=raw_fight.get("difficulty") or 0,
            duration_s=duration_s,
            kill=bool(raw_fight.get("kill")),
        )
        build = PlayerBuild(
            character_name=player,
            server=match.server,
            class_name=match.class_name,
            spec_name=match.spec_name,
            role=match.role,  # type: ignore[arg-type]
            item_level=match.item_level,
            talent_hash=None,
            tier_pieces=None,
        )
        return PlayerLog(
            fight=fight,
            build=build,
            dps=dps,
            percentile=percentile,
            cast_timeline=cast_timeline,
        )

    def _fetch_cast_timeline(
        self,
        report_code: str,
        fight_id: int,
        player_id: int,
        start_time_ms: float,
        end_time_ms: float,
    ) -> dict[int, tuple[float, ...]]:
        timeline_by_id: dict[int, list[float]] = {}
        current_start = start_time_ms
        while current_start < end_time_ms:
            try:
                ev_res_json = self._query(
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
                break

            ev_data = (
                ev_res_json.get("data", {})
                .get("reportData", {})
                .get("report", {})
                .get("events", {})
            )
            page_timeline = parse_cast_events(ev_data.get("data", []), player_id, start_time_ms)
            for spell_id, times in page_timeline.items():
                timeline_by_id.setdefault(spell_id, []).extend(times)

            next_page = ev_data.get("nextPageTimestamp")
            if not next_page or next_page <= current_start or next_page >= end_time_ms:
                break
            current_start = next_page

        return {sid: tuple(times) for sid, times in timeline_by_id.items()}

    def _fetch_percentile(
        self,
        *,
        report_code: str,
        fight_id: int,
        player: str,
        server: str | None,
        region: str | None,
        encounter_id: int,
        difficulty: int | None,
    ) -> float | None:
        """Best-effort: None (never fabricated) on any missing input or
        API failure — see find_matching_rank_percent for the real source.
        """
        if not server or not region or not difficulty:
            return None

        server_slug = server.lower().replace(" ", "-")
        try:
            res_json = self._query(
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
