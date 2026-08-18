"""T1.6/T1.7 — the analysis orchestrator, replacing bot.py's run_analysis.

`Deps` is a plain container of injected dependencies (client, fetcher,
store, catalog, settings) — no singletons, no module-level globals (§1.3).
Every failure path is a specific exception from errors.py; the boundary
layers (bot/discord_bot.py, cli.py) are the only places allowed to catch
them broadly and translate to a user-facing message.

T1.7: the cohort is now identity-addressed by CohortCriteria/cohort_id
(T1.5) and looked up in the Store before ever hitting the live rankings
API. `allow_cold_build=False` (used by the Discord path) turns a cache
miss into CohortNotReady instead of a synchronous 100-log fetch — "o
caminho interativo... nunca baixa 100 logs de forma síncrona".
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from botgitgud.analysis.cohort import (
    POSITIONAL_MIN_N,
    classify_cohort_size,
    duration_bucket_bounds,
    duration_bucket_id,
)
from botgitgud.analysis.comparison import SpellComparison, compare_all_spells
from botgitgud.analysis.profile import build_cd_reference_profile, discover_eligible_spell_ids
from botgitgud.config import Settings
from botgitgud.domain.models import CohortCriteria, CohortProfile, RunManifest
from botgitgud.domain.specs import SpecId, SpecSupport, classify_spec, rejection_message
from botgitgud.domain.spells import SpellCatalog
from botgitgud.errors import CohortNotReady, ScopeRejected
from botgitgud.ingest.log_fetcher import LogFetcher
from botgitgud.ingest.rankings import (
    fetch_cohort_logs,
    fetch_ranking_candidates,
    get_current_partition,
)
from botgitgud.ingest.store import Store
from botgitgud.report.text import ReportHeader
from botgitgud.runmanifest import build_run_manifest
from botgitgud.wcl.client import WclClient

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Deps:
    client: WclClient
    fetcher: LogFetcher
    store: Store
    catalog: SpellCatalog
    settings: Settings


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    report_code: str
    fight_id: int
    character_name: str


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    header: ReportHeader
    comparisons: tuple[SpellComparison, ...]
    manifest: RunManifest


def run_analysis(
    req: AnalysisRequest, deps: Deps, *, allow_cold_build: bool = True
) -> AnalysisResult:
    """Raises PlayerNotFound/FightNotFound (LogFetcher.fetch), ScopeRejected
    (out-of-scope spec), CohortNotReady (no cached profile and
    allow_cold_build=False), or InsufficientCohort (too few ranking
    candidates, cold path only) on every expected failure path.
    """
    player_log = deps.fetcher.fetch(req.report_code, req.fight_id, req.character_name)

    # T0.9: the scope gate runs immediately after identifying the spec,
    # BEFORE any ranking query — no API points spent on out-of-scope input.
    spec_id = SpecId(class_name=player_log.build.class_name, spec_name=player_log.build.spec_name)
    support = classify_spec(spec_id)
    if support != SpecSupport.SUPPORTED:
        message = rejection_message(support, spec_id) or "spec fora de escopo"
        raise ScopeRejected(message)

    partition = get_current_partition(deps.client, player_log.fight.encounter_id)
    bucket_lo, bucket_hi = duration_bucket_bounds(duration_bucket_id(player_log.fight.duration_s))
    criteria = CohortCriteria(
        encounter_id=player_log.fight.encounter_id,
        difficulty=player_log.fight.difficulty,
        partition=partition,
        class_name=player_log.build.class_name,
        spec_name=player_log.build.spec_name,
        metric="dps",
        duration_min_s=bucket_lo,
        duration_max_s=bucket_hi,
    )
    cohort_id = criteria.cohort_id()
    cached_profile = deps.store.read_profile(cohort_id)

    if cached_profile is not None:
        profile = cached_profile.spells
        num_positional = cached_profile.n_members
        duration_min_s, duration_max_s = bucket_lo, bucket_hi
        cohort_median_dps = None  # not tracked by a persisted CohortProfile
        cohort_size_status = classify_cohort_size(cached_profile.n_members)
    else:
        if not allow_cold_build:
            msg = (
                f"coorte ainda não construída para encontro {criteria.encounter_id} "
                f"({criteria.class_name}/{criteria.spec_name}, dif. {criteria.difficulty})"
            )
            raise CohortNotReady(msg)

        candidates = fetch_ranking_candidates(
            deps.client,
            encounter_id=player_log.fight.encounter_id,
            class_name=player_log.build.class_name,
            spec_name=player_log.build.spec_name,
            partition=partition,
            target_duration_s=player_log.fight.duration_s,
        )
        reference_logs = fetch_cohort_logs(
            deps.fetcher, candidates, max_workers=deps.settings.max_workers
        )
        profile, num_positional = build_cd_reference_profile(
            reference_logs, player_log.fight.duration_s
        )
        durations = [rl.fight.duration_s for rl in reference_logs]
        dps_values = [rl.dps for rl in reference_logs if rl.dps is not None]
        cohort_median_dps = statistics.median(dps_values) if dps_values else None
        duration_min_s = min(durations) if durations else player_log.fight.duration_s
        duration_max_s = max(durations) if durations else player_log.fight.duration_s
        cohort_size_status = classify_cohort_size(len(reference_logs))

        # Persisted so the next request for this same bucket — interactive
        # or another build-cohort run — reuses it instead of refetching.
        deps.store.write_profile(
            CohortProfile(
                cohort_id=cohort_id,
                n_members=num_positional,
                built_at=datetime.now(UTC),
                spells=profile,
            )
        )

    eligible_ids = discover_eligible_spell_ids(profile)
    comparisons = compare_all_spells(
        player_log, profile, eligible_ids, catalog=deps.catalog, reference_n=num_positional
    )

    warnings: list[str] = []
    if cohort_size_status == "warn":
        warnings.append(
            f"Amostra pequena ({num_positional} logs). "
            "Trate os desvios como indicativos, não conclusivos."
        )
    if 0 < num_positional < POSITIONAL_MIN_N:
        warnings.append(
            f"Apenas {num_positional} logs com duração próxima à sua (±12%) para "
            "comparar o timing dos cooldowns — os valores 'Ideal' têm confiança baixa."
        )

    # T0.4/T0.7: persisted once, at the end, on the calling thread — never
    # during the concurrent reference-log fetch (achado 4.1).
    deps.catalog.flush()

    header = ReportHeader(
        char_name=player_log.build.character_name,
        boss_name=player_log.fight.boss_name,
        class_name=player_log.build.class_name,
        spec=player_log.build.spec_name,
        reference_n=num_positional,
        duration_min_s=duration_min_s,
        duration_max_s=duration_max_s,
        player_dps=player_log.dps,
        player_percentile=player_log.percentile,
        cohort_median_dps=cohort_median_dps,
        cohort_warnings=tuple(warnings),
    )
    manifest = build_run_manifest(
        cohort_id=cohort_id,
        n_members=num_positional,
        wcl_partition=partition,
        settings=deps.settings,
    )
    deps.store.write_run(manifest)

    return AnalysisResult(header=header, comparisons=tuple(comparisons), manifest=manifest)
