"""T1.6 — the analysis orchestrator, replacing bot.py's run_analysis.

`Deps` is a plain container of injected dependencies (client, fetcher,
store, catalog, settings) — no singletons, no module-level globals (§1.3).
Every failure path is a specific exception from errors.py; the boundary
layers (bot/discord_bot.py, cli.py) are the only places allowed to catch
them broadly and translate to a user-facing message.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

import structlog

from botgitgud.analysis.cohort import POSITIONAL_MIN_N, classify_cohort_size
from botgitgud.analysis.comparison import SpellComparison, compare_all_spells
from botgitgud.analysis.profile import build_cd_reference_profile, discover_eligible_spell_ids
from botgitgud.config import Settings
from botgitgud.domain.specs import SpecId, SpecSupport, classify_spec, rejection_message
from botgitgud.domain.spells import SpellCatalog
from botgitgud.errors import ScopeRejected
from botgitgud.ingest.log_fetcher import LogFetcher
from botgitgud.ingest.rankings import fetch_cohort_logs, fetch_ranking_candidates
from botgitgud.ingest.store import Store
from botgitgud.report.text import ReportHeader
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


def run_analysis(req: AnalysisRequest, deps: Deps) -> AnalysisResult:
    """Raises PlayerNotFound/FightNotFound (LogFetcher.fetch), ScopeRejected
    (out-of-scope spec), or InsufficientCohort (too few ranking candidates
    within the sanity band) on every expected failure path.
    """
    player_log = deps.fetcher.fetch(req.report_code, req.fight_id, req.character_name)

    # T0.9: the scope gate runs immediately after identifying the spec,
    # BEFORE any ranking query — no API points spent on out-of-scope input.
    spec_id = SpecId(class_name=player_log.build.class_name, spec_name=player_log.build.spec_name)
    support = classify_spec(spec_id)
    if support != SpecSupport.SUPPORTED:
        message = rejection_message(support, spec_id) or "spec fora de escopo"
        raise ScopeRejected(message)

    candidates = fetch_ranking_candidates(
        deps.client,
        encounter_id=player_log.fight.encounter_id,
        class_name=player_log.build.class_name,
        spec_name=player_log.build.spec_name,
        target_duration_s=player_log.fight.duration_s,
    )
    reference_logs = fetch_cohort_logs(
        deps.fetcher, candidates, max_workers=deps.settings.max_workers
    )

    profile, num_positional = build_cd_reference_profile(
        reference_logs, player_log.fight.duration_s
    )
    eligible_ids = discover_eligible_spell_ids(profile)
    comparisons = compare_all_spells(
        player_log, profile, eligible_ids, catalog=deps.catalog, reference_n=num_positional
    )

    durations = [rl.fight.duration_s for rl in reference_logs]
    dps_values = [rl.dps for rl in reference_logs if rl.dps is not None]
    cohort_median_dps = statistics.median(dps_values) if dps_values else None

    warnings: list[str] = []
    if classify_cohort_size(len(reference_logs)) == "warn":
        warnings.append(
            f"Amostra pequena ({len(reference_logs)} logs). "
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
        duration_min_s=min(durations) if durations else player_log.fight.duration_s,
        duration_max_s=max(durations) if durations else player_log.fight.duration_s,
        player_dps=player_log.dps,
        player_percentile=player_log.percentile,
        cohort_median_dps=cohort_median_dps,
        cohort_warnings=tuple(warnings),
    )
    return AnalysisResult(header=header, comparisons=tuple(comparisons))
