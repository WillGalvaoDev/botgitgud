"""T1.4 — cached log ingestion, the fix for achado 4.2 (the project's
biggest architectural flaw): every fetch_player_timeline_data() call in
Fase 0's bot.py hits the live API, even for reference players whose logs
never change. LogFetcher checks the Store first; a finished fight's log is
immutable forever (Store's own principle, T1.3), so a cache hit costs zero
API points.

_fetch_from_api ports bot.py's fetch_player_timeline_data (T0.7) +
fetch_player_percentile query logic onto the T1.2 domain models, adding
PlayerNotFound/FightNotFound instead of bot.py's legacy None-returning
pattern — LogFetcher.fetch() always returns a PlayerLog or raises.

Fields Fase 0 never resolved (talent_hash, tier_pieces, external_buffs,
damage_by_ability, uptimes, resource_waste, active_time_pct, deaths,
partition) are left at their PlayerLog/PlayerBuild/FightRef defaults —
T2.1/T2.2/T3.1/T1.7 territory, not this task's job.
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
from botgitgud.wcl.client import WclClient

log = structlog.get_logger(__name__)

# Measured live against the real API (docs/schema_confirmado.md §2/§11):
# every query costs ~2.0 points regardless of complexity. Used to estimate
# api_points_spent from a counted number of queries, since WclClient's own
# points_remaining is a periodically-cached snapshot (T0.3), not a live
# running counter, and can't reliably produce an accurate before/after delta
# for a batch that completes faster than its cache TTL.
_POINTS_PER_QUERY = 2.0

_ROLE_GROUP_TO_ROLE = {"dps": "dps", "healers": "healer", "tanks": "tank"}

_QUERY_META = """
query GetPlayerMeta($code: String!, $fightIDs: [Int]!) {
  reportData {
    report(code: $code) {
      fights(fightIDs: $fightIDs) {
        id encounterID name startTime endTime kill difficulty
      }
      table(fightIDs: $fightIDs, dataType: Summary, translate: true)
      castsTable: table(fightIDs: $fightIDs, dataType: Casts, translate: true)
    }
  }
}
"""

_QUERY_EVENTS = """
query GetPlayerEvents(
  $code: String!, $fightIDs: [Int]!, $startTime: Float!, $endTime: Float!
) {
  reportData {
    report(code: $code) {
      events(
        fightIDs: $fightIDs, dataType: Casts, startTime: $startTime,
        endTime: $endTime, limit: 5000, translate: true
      ) {
        data
        nextPageTimestamp
      }
    }
  }
}
"""

_QUERY_PERCENTILE = """
query GetPercentile(
  $name: String!, $serverSlug: String!, $serverRegion: String!,
  $encounterID: Int!, $difficulty: Int!
) {
  characterData {
    character(name: $name, serverSlug: $serverSlug, serverRegion: $serverRegion) {
      encounterRankings(encounterID: $encounterID, metric: dps, difficulty: $difficulty)
    }
  }
}
"""


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
                    player_log = future.result()
                    results[i] = player_log
                    fetched_logs.append(player_log)

        # T1.4's own rule: no disk writes inside worker threads — persist
        # here, on the calling thread, after every future has resolved.
        for player_log in fetched_logs:
            self._store.write_log(player_log)

        with self._query_count_lock:
            queries_made = self._query_count
        wall_time_s = time.monotonic() - start
        cache_misses = len(fetched_logs)

        log.info(
            "log_fetcher.fetch_many",
            cache_hits=cache_hits,
            cache_misses=cache_misses,
            api_points_spent=queries_made * _POINTS_PER_QUERY,
            wall_time_s=round(wall_time_s, 3),
        )

        return [results[i] for i in range(len(refs))]

    # -- internal ---------------------------------------------------------------

    def _query(self, query: str, variables: dict[str, object], *, op_name: str) -> dict:
        with self._query_count_lock:
            self._query_count += 1
        return self._client.query(query, variables, op_name=op_name)

    def _fetch_from_api(self, report_code: str, fight_id: int, player: str) -> PlayerLog:
        res_json = self._query(
            _QUERY_META, {"code": report_code, "fightIDs": [fight_id]}, op_name="fetch_player_meta"
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

        player_id = None
        user_class = user_spec = user_server = user_region = None
        role: str | None = None
        item_level: float | None = None
        for role_group, mapped_role in _ROLE_GROUP_TO_ROLE.items():
            for p in player_details.get(role_group, []):
                if p.get("name", "").lower() == player.lower():
                    player_id = p.get("id")
                    user_class = p.get("type")
                    user_server = p.get("server")
                    user_region = p.get("region")
                    role = mapped_role
                    item_level = p.get("maxItemLevel")
                    specs = p.get("specs", [])
                    user_spec = (
                        specs[0].get("spec")
                        if specs and isinstance(specs[0], dict)
                        else (specs[0] if specs else None)
                    )
                    break
            if player_id is not None:
                break

        if player_id is None:
            msg = f"jogador '{player}' não encontrado no fight {fight_id} do report {report_code}"
            raise PlayerNotFound(msg)

        damage_total = None
        for entry in summary_data.get("damageDone", []):
            if entry.get("id") == player_id:
                damage_total = entry.get("total")
                break

        casts_entries = report.get("castsTable", {}).get("data", {}).get("entries", [])
        for entry in casts_entries:
            if entry.get("id") == player_id:
                for ab in entry.get("abilities", []):
                    guid = ab.get("guid") or ab.get("id")
                    name = ab.get("name")
                    if guid and name:
                        self._catalog.learn(int(guid), name, "wcl")

        timeline_by_id: dict[int, list[float]] = {}
        current_start = start_time_ms
        while current_start < end_time_ms:
            try:
                ev_res_json = self._query(
                    _QUERY_EVENTS,
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
            for ev in ev_data.get("data", []):
                if ev.get("sourceID") == player_id and ev.get("type") == "cast":
                    spell_id = ev.get("abilityGameID") or ev.get("ability")
                    if not spell_id:
                        continue
                    rel_sec = round(
                        (ev.get("timestamp", start_time_ms) - start_time_ms) / 1000.0, 1
                    )
                    timeline_by_id.setdefault(int(spell_id), []).append(rel_sec)

            next_page = ev_data.get("nextPageTimestamp")
            if not next_page or next_page <= current_start or next_page >= end_time_ms:
                break
            current_start = next_page

        cast_timeline = {sid: tuple(times) for sid, times in timeline_by_id.items()}
        dps = (damage_total / duration_s) if (damage_total and duration_s > 0) else None

        percentile = self._fetch_percentile(
            report_code=report_code,
            fight_id=fight_id,
            player=player,
            server=user_server,
            region=user_region,
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
            server=user_server,
            class_name=user_class or "Unknown",
            spec_name=user_spec or "Unknown",
            role=role or "dps",  # type: ignore[arg-type]
            item_level=item_level,
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
        API failure. See docs/schema_confirmado.md §9 — the real percentile
        comes from characterData.character.encounterRankings, matched by
        report code + fightID; characterRankings itself has no percentile
        field (achado 3.10).
        """
        if not server or not region or not difficulty:
            return None

        server_slug = server.lower().replace(" ", "-")
        try:
            res_json = self._query(
                _QUERY_PERCENTILE,
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
        if not character:
            return None

        for rank in (character.get("encounterRankings") or {}).get("ranks") or []:
            rep = rank.get("report", {})
            if rep.get("code") == report_code and rep.get("fightID") == fight_id:
                return rank.get("rankPercent")
        return None
