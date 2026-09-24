"""T1.4 — cached log ingestion (achado 4.2): the Store is checked before
any network call, since a finished fight's log is immutable (T1.3), so a
cache hit costs zero API points. fetch() raises PlayerNotFound/
FightNotFound rather than returning None. JSON parsing lives in
ingest/wcl_parsing.py (T1.6 split).
"""

from __future__ import annotations

import contextvars
import functools
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from typing import Any

import structlog

from botgitgud import telemetry
from botgitgud.analysis.phases import derive_phase_intervals
from botgitgud.domain.damage_scope import DamageScopeVersion
from botgitgud.domain.models import FightRef, MeasurementProvenance, PlayerBuild, PlayerLog
from botgitgud.domain.spells import SpellCatalog
from botgitgud.errors import ApiError, FightNotFound, PlayerNotFound, RateLimitBudgetExceeded
from botgitgud.ingest.fight_rankings import fetch_partition
from botgitgud.ingest.log_fetcher_aux import (
    fetch_buffs_and_debuffs,
    fetch_cast_timelines,
    fetch_percentile,
)
from botgitgud.ingest.performance_fetch import (
    fetch_resource_waste,
    fetch_scoped_damage_and_targets,
)
from botgitgud.ingest.performance_parsing import (
    compute_active_time_pct,
    compute_downtime_s,
    extract_pet_owner_map,
    find_damage_table_entry,
    parse_death_events,
    parse_resource_waste_by_ability,
    pet_ids_for_owner,
)
from botgitgud.ingest.store import Store
from botgitgud.ingest.wcl_parsing import (
    extract_ambiguous_scope_target_groups,
    extract_damage_table_total,
    extract_damage_total,
    extract_scope_target_ids,
    find_player_in_details,
    learn_spells_from_casts_table,
    learn_spells_from_damage_table,
    learn_spells_from_master_data,
)
from botgitgud.wcl.client import WclClient
from botgitgud.wcl.queries import QUERY_PLAYER_META

log = structlog.get_logger(__name__)

# Measured live (docs/schema_confirmado.md §2/§11): every query costs ~2.0
# points regardless of complexity. Estimates api_points_spent from a query
# count, since WclClient.points_remaining is a periodically-cached
# snapshot (T0.3), not a reliable before/after delta for a fast batch.
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

    @property
    def query_count(self) -> int:
        """Monotonic query count for single-threaded campaign accounting."""
        with self._query_count_lock:
            return self._query_count

    def fetch(
        self,
        report_code: str,
        fight_id: int,
        player: str,
        *,
        force: bool = False,
        experimental_label: float | None = None,
        fight_query_cache: dict[tuple[str, str], dict[str, Any]] | None = None,
    ) -> PlayerLog:
        if not force:
            cached = self._store.read_log(report_code, fight_id, player)
            if cached is not None:
                if experimental_label is None or cached.percentile == experimental_label:
                    return cached
                labelled = replace(cached, percentile=experimental_label)
                self._store.write_log(labelled)
                return labelled

        player_log = self._fetch_from_api(
            report_code,
            fight_id,
            player,
            experimental_label=experimental_label,
            fight_query_cache=fight_query_cache,
        )
        self._store.write_log(player_log)
        return player_log

    def fetch_many(
        self,
        refs: Sequence[LogRequest],
        *,
        max_workers: int,
        expected_partition: int | None = None,
    ) -> list[PlayerLog]:
        """Best-effort: a ref that fails (PlayerNotFound/FightNotFound/
        ApiError) is skipped rather than aborting the batch (some ranked
        logs are expected to 404). RateLimitBudgetExceeded is different —
        see below. Result preserves `refs` order, may be shorter.
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
        budget_exceeded: RateLimitBudgetExceeded | None = None
        if to_fetch:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                for i, ref in to_fetch:
                    # Cada worker precisa de sua propria copia: Context nao
                    # pode ser entrado concorrentemente e ThreadPoolExecutor
                    # nao propaga ContextVars automaticamente.
                    context = contextvars.copy_context()
                    if expected_partition is None:
                        future = executor.submit(
                            context.run,
                            self._fetch_from_api,
                            ref.report_code,
                            ref.fight_id,
                            ref.player,
                        )
                    else:
                        fetch = functools.partial(
                            self._fetch_from_api,
                            ref.report_code,
                            ref.fight_id,
                            ref.player,
                            expected_partition=expected_partition,
                        )
                        future = executor.submit(context.run, fetch)
                    futures[future] = i
                for future in as_completed(futures):
                    i = futures[future]
                    try:
                        player_log = future.result()
                    except RateLimitBudgetExceeded as e:
                        # Global, not per-item — re-raised below, never
                        # swallowed as a per-ref failure (T1.7).
                        budget_exceeded = budget_exceeded or e
                        continue
                    except (PlayerNotFound, FightNotFound, ApiError) as e:
                        failures += 1
                        log.warning("log_fetcher.fetch_many_ref_failed", index=i, error=str(e))
                        continue
                    results[i] = player_log
                    fetched_logs.append(player_log)

        # No disk writes inside worker threads (T1.4). Runs even on
        # budget_exceeded (T1.7: "salva o progresso parcial").
        for player_log in fetched_logs:
            self._store.write_log(player_log)

        telemetry.record_reference_batch(
            expected=len(refs), cache_hits=cache_hits, fetched=len(fetched_logs)
        )
        with self._query_count_lock:
            queries_made = self._query_count
        wall_time_s = time.monotonic() - start

        log.info(
            "log_fetcher.fetch_many",
            cache_hits=cache_hits,
            cache_misses=len(fetched_logs),
            failures=failures,
            budget_exceeded=budget_exceeded is not None,
            api_points_spent=queries_made * _POINTS_PER_QUERY,
            wall_time_s=round(wall_time_s, 3),
        )

        if budget_exceeded is not None:
            raise budget_exceeded

        return [results[i] for i in range(len(refs)) if i in results]

    # -- internal ---------------------------------------------------------------

    def _query(self, query: str, variables: dict[str, object], *, op_name: str) -> dict:
        with self._query_count_lock:
            self._query_count += 1
        return self._client.query(query, variables, op_name=op_name)

    def _fetch_from_api(
        self,
        report_code: str,
        fight_id: int,
        player: str,
        *,
        experimental_label: float | None = None,
        fight_query_cache: dict[tuple[str, str], dict[str, Any]] | None = None,
        expected_partition: int | None = None,
    ) -> PlayerLog:
        def query_fn(query: str, variables: dict[str, object], *, op_name: str) -> dict:
            fight_wide = {
                "fetch_player_meta",
                "fetch_player_events",
                "fetch_player_damage_events",
                "fetch_player_resource_events",
                "fetch_report_rankings",
            }
            cache_key = (query, repr(sorted(variables.items())))
            if fight_query_cache is not None and op_name in fight_wide:
                cached_response = fight_query_cache.get(cache_key)
                if cached_response is not None:
                    return cached_response
            response = self._query(query, variables, op_name=op_name)
            if fight_query_cache is not None and op_name in fight_wide:
                fight_query_cache[cache_key] = response
            return response

        res_json = query_fn(
            QUERY_PLAYER_META,
            {"code": report_code, "fightIDs": [fight_id]},
            op_name="fetch_player_meta",
        )
        report = res_json.get("data", {}).get("reportData", {}).get("report", {})
        learn_spells_from_master_data(
            report.get("masterData", {}).get("abilities", []), self._catalog
        )
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
        damage_entries = report.get("damageTable", {}).get("data", {}).get("entries", [])
        learn_spells_from_damage_table(damage_entries, self._catalog)

        phase_intervals = derive_phase_intervals(
            raw_fight.get("phaseTransitions") or [],
            fight_start_ms=start_time_ms,
            fight_end_ms=end_time_ms,
        )
        cast_timeline, phase_cast_timeline, casts_provenance = fetch_cast_timelines(
            query_fn,
            report_code=report_code,
            fight_id=fight_id,
            player_id=match.player_id,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            intervals=phase_intervals,
            include_provenance=True,
        )
        dps = (damage_total / duration_s) if (damage_total and duration_s > 0) else None

        percentile = experimental_label
        if percentile is None:
            percentile = fetch_percentile(
                query_fn,
                report_code=report_code,
                fight_id=fight_id,
                player=player,
                server=match.server,
                region=match.region,
                encounter_id=raw_fight["encounterID"],
                difficulty=raw_fight.get("difficulty"),
            )
        has_augmentation, external_buffs, uptimes, aura_details = fetch_buffs_and_debuffs(
            query_fn,
            report_code=report_code,
            fight_id=fight_id,
            player_id=match.player_id,
            catalog=self._catalog,
        )

        # T-DG.0: report.rankings is the strong partition source — never
        # fabricated when unavailable, stays None as before (D-12(a)).
        partition = expected_partition
        if partition is None:
            partition = fetch_partition(query_fn, report_code=report_code, fight_id=fight_id)

        # T3.1 — features beyond casts.
        damage_entry = find_damage_table_entry(damage_entries, match.player_id)
        active_time_pct = compute_active_time_pct(damage_entry, end_time_ms - start_time_ms)

        actors = report.get("masterData", {}).get("actors", [])
        pet_owner_map = extract_pet_owner_map(actors)
        pet_ids = pet_ids_for_owner(pet_owner_map, match.player_id)
        cast_counts = {spell_id: len(times) for spell_id, times in cast_timeline.items()}
        scope_target_ids = extract_scope_target_ids(damage_entries, actors)
        (
            damage_by_ability,
            _avg_targets_per_cast,
            support_subtracted,
            scoped_total,
            unscoped_own,
            damaged_target_ids,
            damage_provenance,
            event_mix,
        ) = fetch_scoped_damage_and_targets(
            query_fn,
            report_code=report_code,
            fight_id=fight_id,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            player_id=match.player_id,
            source_ids=frozenset({match.player_id}) | pet_ids,
            target_ids=scope_target_ids,
            pet_owner_by_actor=pet_owner_map,
            cast_counts=cast_counts,
            include_provenance=True,
        )
        authoritative_total = extract_damage_table_total(damage_entries, match.player_id)
        ambiguous_groups = extract_ambiguous_scope_target_groups(damage_entries, actors)
        damaging_ambiguity = any(len(group & damaged_target_ids) > 1 for group in ambiguous_groups)
        reconcilable = (
            authoritative_total is not None
            and bool(scope_target_ids)
            and unscoped_own >= authoritative_total
        )
        if not reconcilable:
            # Missing table authority, target scope, or a complete event stream:
            # preserve historical semantics rather than calling absence a mismatch.
            damage_scope = DamageScopeVersion.LEGACY_UNSCOPED
        elif not damaging_ambiguity and scoped_total == authoritative_total:
            damage_scope = DamageScopeVersion.WCL_TARGET_SCOPE_V1
        else:
            damage_scope = DamageScopeVersion.UNRECONCILED
        resource_events: list[dict[str, Any]] = []

        def recording_resource_query(*args: Any, **kwargs: Any) -> dict[str, Any]:
            response = query_fn(*args, **kwargs)
            event_data = (
                response.get("data", {})
                .get("reportData", {})
                .get("report", {})
                .get("events", {})
                .get("data", [])
            )
            resource_events.extend(e for e in event_data if isinstance(e, dict))
            return response

        resource_waste = fetch_resource_waste(
            recording_resource_query,
            report_code=report_code,
            fight_id=fight_id,
            player_id=match.player_id,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )
        resource_waste_by_ability = parse_resource_waste_by_ability(
            resource_events, match.player_id
        )

        death_times_ms = parse_death_events(summary_data.get("deathEvents", []), match.player_id)
        all_cast_times_s = [t for times in cast_timeline.values() for t in times]
        downtime_s = compute_downtime_s(death_times_ms, all_cast_times_s, duration_s)

        fight = FightRef(
            report_code=report_code,
            fight_id=fight_id,
            encounter_id=raw_fight["encounterID"],
            boss_name=raw_fight["name"],
            difficulty=raw_fight.get("difficulty") or 0,
            duration_s=duration_s,
            kill=bool(raw_fight.get("kill")),
            partition=partition,
            phase_intervals=phase_intervals,
        )
        build = PlayerBuild(
            character_name=player,
            server=match.server,
            class_name=match.class_name,
            spec_name=match.spec_name,
            role=match.role,  # type: ignore[arg-type]
            item_level=match.item_level,
            talent_hash=match.talent_hash,
            tier_pieces=match.tier_pieces,
            external_buffs=external_buffs,
            has_augmentation=has_augmentation,
            talent_pairs=match.talent_pairs,
            setup=match.setup,
        )
        return PlayerLog(
            fight=fight,
            build=build,
            dps=dps,
            percentile=percentile,
            cast_timeline=cast_timeline,
            active_time_pct=active_time_pct,
            damage_by_ability=damage_by_ability,
            uptimes=uptimes,
            resource_waste=resource_waste,
            resource_waste_by_ability=resource_waste_by_ability,
            aura_details=aura_details,
            deaths=len(death_times_ms),
            downtime_s=downtime_s,
            avg_targets_per_cast={},
            phase_cast_timeline=phase_cast_timeline,
            damage_scope=damage_scope,
            support_subtracted_damage=(
                support_subtracted
                if damage_scope is DamageScopeVersion.WCL_TARGET_SCOPE_V1
                else 0.0
            ),
            measurement_provenance=MeasurementProvenance(
                damage_collection=damage_provenance,
                casts_collection=casts_provenance,
                damage_table_total=authoritative_total,
                player_actor_id=match.player_id,
                pet_actor_ids=tuple(sorted(pet_ids)),
                damage_event_mix_by_spell=event_mix,
                damage_reconciliation_status=damage_scope.value,
            ),
        )
