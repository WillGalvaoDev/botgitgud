"""T1.6 — end-to-end tests for analysis/pipeline.py's run_analysis, the
orchestrator that replaces bot.py's run_analysis. Exercises the real
WclClient/LogFetcher/Store/SpellCatalog stack against a fake httpx
transport (no network), the same pattern as test_log_fetcher.py.

test_scope_rejection_triggers_zero_ranking_queries is this task's
replacement for the old test_bot_scope_gate.py (which exercised the
now-deleted root bot.py directly): same invariant — an out-of-scope spec
must never spend a ranking-query API point — verified against the real
pipeline instead of a monkeypatched module.
"""

from __future__ import annotations

import json
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_log_fetcher import (
    _events_response,
    _meta_response,
    _percentile_response,
    _report_rankings_response,
)

import botgitgud.analysis.pipeline as pipeline_module
from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget
from botgitgud.analysis.benchmark_aggregate import (
    CANONICAL_SECONDARY_STATS,
    BandBenchmark,
    CoverageSummary,
    DescriptiveStats,
    EncounterBenchmark,
    PrevalenceDistribution,
    PrevalenceEntry,
    SetSummary,
    talent_build_key,
)
from botgitgud.analysis.benchmark_store import BenchmarkStore
from botgitgud.analysis.pipeline import AnalysisRequest, Deps, run_analysis
from botgitgud.analysis.setup_finding import FindingCategory, ObservationCode, Publicability
from botgitgud.config import Settings
from botgitgud.domain.models import SetupProfile, TalentNode
from botgitgud.domain.specs import SpecId
from botgitgud.domain.spells import SpellCatalog
from botgitgud.errors import (
    CohortDeferredBudget,
    CohortNotReady,
    InsufficientCohort,
    PlayerNotFound,
    ScopeRejected,
)
from botgitgud.ingest.log_fetcher import LogFetcher
from botgitgud.ingest.store import Store
from botgitgud.phase4.registry import ModelStatus, Phase4ModelRecord, Phase4ModelRegistry
from botgitgud.phase4.resolver import Phase4ModelResolver, ResolutionStatus
from botgitgud.phase4.target import Phase4Target
from botgitgud.report.text import render_report
from botgitgud.wcl.client import WclClient, WclClientConfig

PRIMARY_REPORT = "ABCDEFGHIJKLMNOP"
PRIMARY_FIGHT = 1
N_REFS = 8  # COHORT_MIN_HARD


def _rate_limit_response(points_spent: float = 0.0) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": {
                "rateLimitData": {
                    "limitPerHour": 10000,
                    "pointsSpentThisHour": points_spent,
                    "pointsResetIn": 3600,
                }
            }
        },
    )


def _rankings_response(n: int, *, has_more: bool = False) -> dict[str, Any]:
    rankings = [
        {
            "name": f"Ref{i}",
            "duration": 100000,
            "report": {"code": f"REFCODE{i:09d}", "fightID": 1},
        }
        for i in range(n)
    ]
    return {
        "data": {
            "worldData": {
                "encounter": {"characterRankings": {"rankings": rankings, "hasMorePages": has_more}}
            }
        }
    }


def _buffs_response(aura_guids: list[int] | None = None) -> dict[str, Any]:
    auras = [
        {"guid": g, "name": f"Aura{g}", "totalUptime": 1000, "totalUses": 1}
        for g in (aura_guids or [])
    ]
    return {
        "data": {
            "reportData": {"report": {"table": {"data": {"auras": auras, "totalTime": 1000.0}}}}
        }
    }


def _zone_partitions_response(*, default_partition: int = 3) -> dict[str, Any]:
    return {
        "data": {
            "worldData": {
                "encounter": {
                    "zone": {
                        "id": 46,
                        "partitions": [
                            {"id": 1, "default": False},
                            {"id": default_partition, "default": True},
                        ],
                    }
                }
            }
        }
    }


class _DispatchTransport(httpx.BaseTransport):
    def __init__(
        self,
        responses: dict[str, list[dict[str, Any]] | dict[str, Any]],
        *,
        points_spent: float = 0.0,
    ) -> None:
        self._responses = responses
        # Sem isto o fixture reporta sempre `available == limitPerHour`, regime
        # em que "adiado agora" e "impossivel por configuracao" coincidem
        # matematicamente e nao da para testar um adiamento de verdade.
        self._points_spent = points_spent
        self.calls: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if "oauth.battle.net" in str(request.url) or "warcraftlogs.com/oauth" in str(request.url):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})

        body = json.loads(request.content)
        query = body.get("query", "")
        if "rateLimitData" in query:
            return _rate_limit_response(self._points_spent)
        if "GetPlayerMeta" in query:
            op = "meta"
        elif "GetPlayerDamageEvents" in query:
            op = "damage_events"
        elif "GetPlayerResourceEvents" in query:
            op = "resource_events"
        elif "GetPlayerEvents" in query:
            op = "events"
        elif "GetPercentile" in query:
            op = "percentile"
        elif "GetReportRankings" in query:
            op = "report_rankings"
        elif "GetRankingsCDs" in query:
            op = "rankings"
        elif "GetZonePartitions" in query:
            op = "partition"
        elif "GetPlayerDebuffs" in query:
            op = "debuffs"
        elif "GetPlayerBuffs" in query:
            op = "buffs"
        else:
            pytest.fail(f"query GraphQL não reconhecida: {query[:80]}")

        self.calls.append(op)
        # T2.1/T3.1: ops with a safe empty default don't need to be spelled
        # out by every test's response dict.
        _empty_events: dict[str, Any] = {
            "data": {"reportData": {"report": {"events": {"data": [], "nextPageTimestamp": None}}}}
        }
        _empty_defaults: dict[str, dict[str, Any]] = {
            "buffs": _buffs_response(),
            "debuffs": _buffs_response(),
            "damage_events": _empty_events,
            "resource_events": _empty_events,
            "report_rankings": _report_rankings_response(no_data=True),
        }
        if op in _empty_defaults and op not in self._responses:
            return httpx.Response(200, json=_empty_defaults[op])
        if op not in self._responses:
            pytest.fail(f"operação '{op}' inesperada — nenhuma resposta canned para ela")

        payload = self._responses[op]
        item = payload.pop(0) if isinstance(payload, list) else payload
        return httpx.Response(200, json=item)


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "discord_token": "d" * 10,
        "wcl_client_id": "id",
        "wcl_client_secret": "secret",
        "blizzard_client_id": "id2",
        "blizzard_client_secret": "secret2",
        "max_workers": 1,  # deterministic response consumption order, see module docstring
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[arg-type, call-arg]


def _req(character_name: str = "Zarad") -> AnalysisRequest:
    return AnalysisRequest(
        report_code=PRIMARY_REPORT, fight_id=PRIMARY_FIGHT, character_name=character_name
    )


def _build_deps(
    tmp_path: Path, transport: httpx.BaseTransport, **settings_overrides: object
) -> Deps:
    client = WclClient(WclClientConfig(client_id="id", client_secret="secret"), transport=transport)
    store = Store(tmp_path / "data")
    catalog = SpellCatalog(tmp_path / "spells.json", blizzard=None)
    fetcher = LogFetcher(client, store, catalog)
    # C.3: sem isto, Settings cai no default de producao (Path("data")) e todo
    # teste que persiste relatorio escreve no data/reports REAL do projeto.
    settings_overrides.setdefault("data_dir", tmp_path / "data")
    settings = _settings(**settings_overrides)
    return Deps(client=client, fetcher=fetcher, store=store, catalog=catalog, settings=settings)


# T2.2: shared across every fixture player in _happy_path_responses so
# tier_pieces (4, matching the wider test suite's convention) and
# talent_cluster strictly match by default — a "happy path" fixture where
# every covariate matches on the strict pass, same intent as item_level's
# shared default (283.0).
_SHARED_COMBATANT_INFO: dict[str, Any] = {
    "talentTree": [{"id": 1, "rank": 1, "nodeID": 100}, {"id": 2, "rank": 1, "nodeID": 101}],
    "gear": [{"slot": i, "setID": 1989} for i in range(4)]
    + [{"slot": i, "setID": None} for i in range(4, 16)],
}


def _happy_path_responses(
    *, class_name: str = "Warlock", spec_name: str = "Demonology"
) -> dict[str, Any]:
    meta = [
        _meta_response(
            class_name=class_name, spec_name=spec_name, combatant_info=_SHARED_COMBATANT_INFO
        )
    ]
    primary_cast = {"sourceID": 6, "type": "cast", "abilityGameID": 104316, "timestamp": 1300}
    events = [_events_response([primary_cast])]
    percentile = [_percentile_response(71.0, PRIMARY_REPORT, PRIMARY_FIGHT)]

    for i in range(N_REFS):
        meta.append(
            _meta_response(
                player_id=100 + i,
                player_name=f"Ref{i}",
                class_name=class_name,
                spec_name=spec_name,
                damage_total=900_000.0,
                combatant_info=_SHARED_COMBATANT_INFO,
            )
        )
        events.append(
            _events_response(
                [{"sourceID": 100 + i, "type": "cast", "abilityGameID": 104316, "timestamp": 1300}]
            )
        )
        percentile.append(_percentile_response(None, f"REFCODE{i:09d}", 1))

    return {
        "meta": meta,
        "events": events,
        "percentile": percentile,
        "rankings": [_rankings_response(N_REFS)],
        "partition": _zone_partitions_response(),  # dict, not list: reusable across calls
        # M2.3: the player's own fight.partition comes from report.rankings
        # (fetch_partition), a separate WCL call from get_current_partition
        # above — reference logs get expected_partition passed directly and
        # never make this call. Without it the player's partition stays None
        # (log_fetcher.py's honest default) while every reference carries a
        # real partition, so M2.1's PARTITION axis would mark every
        # reference PARTITION_UNKNOWN (target side unknown) instead of
        # exercising the real match/mismatch path this fixture intends.
        # 3 matches _zone_partitions_response()'s own default.
        "report_rankings": _report_rankings_response(partition=3),
    }


def test_run_analysis_happy_path_returns_header_and_comparisons(tmp_path: Path) -> None:
    transport = _DispatchTransport(_happy_path_responses())
    deps = _build_deps(tmp_path, transport)
    req = _req()

    result = run_analysis(req, deps)

    assert result.header.char_name == "Zarad"
    assert result.header.class_name == "Warlock"
    assert result.header.spec == "Demonology"
    assert result.header.matched_reference_n == N_REFS
    assert result.header.player_dps == pytest.approx(10000.0)  # 1_000_000 / 100s
    assert result.comparisons
    assert result.dps_gap is not None
    assert not result.dps_gap.quantitative_damage_available
    assert result.phase4_resolution.status is ResolutionStatus.UNAVAILABLE


def test_analysis_result_execution_findings_defaults_to_empty_tuple() -> None:
    """RB-3: `execution_findings` is a new AnalysisResult field with an
    empty default so building one by hand (tests, or any future caller
    that doesn't pass it) never breaks.
    """
    import dataclasses

    field = next(
        f
        for f in dataclasses.fields(pipeline_module.AnalysisResult)
        if f.name == "execution_findings"
    )
    assert field.default == ()

    remediation_field = next(
        f for f in dataclasses.fields(pipeline_module.AnalysisResult) if f.name == "remediations"
    )
    assert remediation_field.default == ()


def test_run_analysis_execution_findings_nonvacuous_with_a_material_death(tmp_path: Path) -> None:
    """M27 acceptance, end-to-end (not just build_findings in isolation):
    deaths/active_time (paired with downtime)/resource_waste reach
    `AnalysisResult.execution_findings` when material. Needs a cohort of
    at least MIN_N_FOR_GRADING (15) all-zero-death references so the
    player's single death actually grades red/yellow instead of
    "insufficient" — the small N_REFS=8 happy-path fixture above stays
    below that floor by design (COHORT_MIN_HARD=8) and would prove
    nothing about materiality, same reasoning as the golden Zarad fixture
    itself (matched n=10, insufficient) in test_findings.py's non-vacuity
    test.
    """
    n_refs = 20
    meta = [
        _meta_response(
            class_name="Warlock",
            spec_name="Demonology",
            combatant_info=_SHARED_COMBATANT_INFO,
            death_events=[{"id": 6, "deathTime": 500.0}],
        )
    ]
    primary_cast = {"sourceID": 6, "type": "cast", "abilityGameID": 104316, "timestamp": 1300}
    events = [_events_response([primary_cast])]
    percentile = [_percentile_response(71.0, PRIMARY_REPORT, PRIMARY_FIGHT)]

    for i in range(n_refs):
        meta.append(
            _meta_response(
                player_id=100 + i,
                player_name=f"Ref{i}",
                class_name="Warlock",
                spec_name="Demonology",
                damage_total=900_000.0,
                combatant_info=_SHARED_COMBATANT_INFO,
            )
        )
        events.append(
            _events_response(
                [{"sourceID": 100 + i, "type": "cast", "abilityGameID": 104316, "timestamp": 1300}]
            )
        )
        percentile.append(_percentile_response(None, f"REFCODE{i:09d}", 1))

    responses: dict[str, Any] = {
        "meta": meta,
        "events": events,
        "percentile": percentile,
        "rankings": [_rankings_response(n_refs)],
        "partition": _zone_partitions_response(),
        "report_rankings": _report_rankings_response(partition=3),
    }
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)

    result = run_analysis(_req(), deps)

    assert result.matched_cohort_members is not None and result.matched_cohort_members >= 15
    assert result.execution_findings != ()
    assert any(f.category == "DEATH" for f in result.execution_findings)
    from botgitgud.analysis.remediation import RemediationBasis, RemediationKind

    death = next(f for f in result.execution_findings if f.category == "DEATH")
    associated = next(item for item in result.remediations if item.finding is death)
    assert associated.coaching_eligible
    assert associated.remediation.kind is RemediationKind.DIRECT_ACTION
    assert associated.remediation.basis is RemediationBasis.OBSERVED_DEATH
    assert associated.remediation.condition is None
    assert all(item.remediation.provenance is None for item in result.remediations)
    assert not any(
        hasattr(f.finding, "observed_deficit_player_pp")
        for f in result.execution_findings  # RB-1
    )


# -- RP.2: Setup Analysis integration --------------------------------------------


def test_setup_analysis_is_always_computed_and_never_blocks_execution(tmp_path: Path) -> None:
    """No Encounter Benchmark has ever been persisted in this fresh Store —
    `setup_analysis` must still be populated (never `None`, never an
    exception), degrading to missing-data findings, while EXECUTION
    (comparisons/header) works exactly as in the plain happy path.
    """
    transport = _DispatchTransport(_happy_path_responses())
    deps = _build_deps(tmp_path, transport)

    result = run_analysis(_req(), deps)

    assert result.setup_analysis is not None
    assert len(result.setup_analysis.findings) > 0
    assert all(
        f.observation is ObservationCode.MISSING_DATA for f in result.setup_analysis.findings
    )
    assert all(f.publicability is Publicability.HIDDEN for f in result.setup_analysis.findings)
    # execução continua funcionando normalmente, sem qualquer degradação:
    assert result.header.char_name == "Zarad"
    assert result.comparisons


def test_setup_analysis_reflects_a_persisted_benchmark(tmp_path: Path) -> None:
    """When an Encounter Benchmark for this exact target already exists in
    the Store, `setup_analysis` must reflect it — proving the read-only
    wiring actually reaches real data, not just the missing-data path.
    """
    transport = _DispatchTransport(_happy_path_responses())
    deps = _build_deps(tmp_path, transport)

    # A mesma identidade que o pipeline vai calcular para este fixture:
    # SpecId(Warlock, Demonology), encounter_id=3179 (default de
    # _meta_response), difficulty=5, partition=3 (default de
    # _zone_partitions_response).
    target = EncounterBenchmarkTarget(
        spec=SpecId("Warlock", "Demonology"), encounter_id=3179, difficulty=5, partition=3
    )
    policy = BenchmarkPolicy.default()

    # A mesma build de talentos que _SHARED_COMBATANT_INFO produz —
    # calculada via a função real, nunca uma string chutada.
    player_key = talent_build_key(
        SetupProfile(
            talents=(
                TalentNode(node_id=100, rank=1, spell_id=1),
                TalentNode(node_id=101, rank=1, spell_id=2),
            )
        )
    )
    assert player_key is not None

    empty_dist = PrevalenceDistribution(n_available=0, entries=())
    empty_stats = DescriptiveStats(n=0, median=None, p25=None, p75=None)
    empty_set = SetSummary(n_available=0, entries=())
    talent_dist = PrevalenceDistribution(
        n_available=50, entries=(PrevalenceEntry(key=player_key, n_observed=40, prevalence=0.8),)
    )
    top_band = BandBenchmark(
        band_name=policy.bands[-1].name,
        status="ok",
        sample_size=50,
        raw_observation_count=50,
        talent_build_prevalence=talent_dist,
        trinket_prevalence=empty_dist,
        trinket_pair_prevalence=empty_dist,
        set_summary=empty_set,
        secondary_stats={s: empty_stats for s in CANONICAL_SECONDARY_STATS},
        duration_summary=empty_stats,
        item_level_summary=empty_stats,
    )
    other_bands = {
        b.name: BandBenchmark(
            band_name=b.name,
            status="insufficient",
            sample_size=0,
            raw_observation_count=0,
            talent_build_prevalence=empty_dist,
            trinket_prevalence=empty_dist,
            trinket_pair_prevalence=empty_dist,
            set_summary=empty_set,
            secondary_stats={s: empty_stats for s in CANONICAL_SECONDARY_STATS},
            duration_summary=empty_stats,
            item_level_summary=empty_stats,
        )
        for b in policy.bands[:-1]
    }
    bands = {**other_bands, policy.bands[-1].name: top_band}
    benchmark = EncounterBenchmark(
        target=target,
        policy_version=policy.policy_version,
        total_input_observations=50,
        eligible_observations=50,
        missing_setup_count=0,
        deduped_count=0,
        outside_policy_bands=0,
        coverage=CoverageSummary(50, 50, 0, 1.0),
        bands=bands,
    )
    BenchmarkStore(deps.store).write_benchmark(benchmark, policy=policy, observations=())

    result = run_analysis(_req(), deps)

    assert result.setup_analysis is not None
    talent_findings = [
        f for f in result.setup_analysis.findings if f.category is FindingCategory.TALENT_BUILD
    ]
    assert len(talent_findings) == 1
    assert talent_findings[0].observation is ObservationCode.MATCHES_COMMON_PATTERN
    assert talent_findings[0].publicability is Publicability.PUBLISHABLE
    # execução continua funcionando, e nenhuma categoria de setup mexeu nela:
    assert result.comparisons


def test_setup_analysis_never_flows_into_execution_computations() -> None:
    """Architectural guard (RP.2's own rules: 'setup findings não entram no
    Top 3'; 'nenhuma categoria de setup altera execution grade'):
    `setup_analysis` must never be passed as an argument to any of the
    functions that compute execution findings/grading/Top 3 — checked via
    AST over every Call node in `run_analysis`, not just documentation.
    """
    import ast
    import inspect

    import botgitgud.analysis.pipeline as pipeline_module

    forbidden_targets = {
        "build_findings",
        "analyze_dps_gap",
        "compare_all_spells",
        "analyze_performance_features",
        "build_cd_reference_profile",
        "match_cohort",
        "select_top_actions",
        "select_top_priorities",
    }
    tree = ast.parse(inspect.getsource(pipeline_module))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id not in forbidden_targets:
            continue
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            if isinstance(arg, ast.Name):
                assert arg.id != "setup_analysis", (
                    f"{node.func.id} must never receive setup_analysis as an argument"
                )


def test_reference_logs_reuse_criteria_partition_without_report_rankings(tmp_path: Path) -> None:
    transport = _DispatchTransport(_happy_path_responses())
    deps = _build_deps(tmp_path, transport)

    result = run_analysis(_req(), deps)

    assert result.manifest.wcl_partition == 3
    # Only the analyzed log resolves its report partition. References inherit
    # the already-known canonical cohort partition and keep their difficulty.
    assert transport.calls.count("report_rankings") == 1
    reference_rows = deps.store.query(
        "SELECT DISTINCT partition, difficulty FROM logs WHERE player_name LIKE 'Ref%'"
    ).rows()
    assert reference_rows == [(3, 5)]


def test_run_analysis_exposes_ready_capability_without_running_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        pipeline_module,
        "CohortCriteria",
        partial(pipeline_module.CohortCriteria, matching_policy_version="v1"),
    )
    transport = _DispatchTransport(_happy_path_responses())
    deps = _build_deps(tmp_path, transport)
    registry = Phase4ModelRegistry(deps.store)
    target = Phase4Target(SpecId("Warlock", "Demonology"), 3179, 5, 3)
    registry.register(
        Phase4ModelRecord(
            target,
            model_version="m1",
            dataset_version="d1",
            number_of_observations=5000,
            artifact_path="models/Warlock/Demonology/3179/5/3/m1.bin",
            status=ModelStatus.READY,
        )
    )
    deps = replace(deps, phase4_resolver=Phase4ModelResolver(registry))

    result = run_analysis(_req(), deps)

    assert result.phase4_resolution.status is ResolutionStatus.FOUND
    assert result.header.char_name == "Zarad"
    assert result.top_actions is not None
    assert result.comparisons
    assert result.manifest.cohort_id
    assert result.manifest.wcl_partition == 3
    assert "has_augmentation" in result.header.matched_covariates
    assert "external_buffs" in result.header.matched_covariates


def test_player_without_augmentation_gets_an_augmentation_free_cohort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T2.1 acceptance: a player without Augmentation in the raid gets a
    cohort of ONLY non-Augmentation logs, as long as n >= COHORT_MIN_HARD
    without needing to relax has_augmentation — 8 clean candidates plus 4
    Augmentation-buffed ones are offered; only the 8 clean ones survive.
    """
    monkeypatch.setattr(
        pipeline_module,
        "CohortCriteria",
        partial(pipeline_module.CohortCriteria, matching_policy_version="v1"),
    )
    n_clean = N_REFS
    n_augmented = 4
    meta = [_meta_response(class_name="Warlock", spec_name="Demonology")]  # primary: no augment
    primary_cast = {"sourceID": 6, "type": "cast", "abilityGameID": 104316, "timestamp": 1300}
    events = [_events_response([primary_cast])]
    percentile = [_percentile_response(71.0, PRIMARY_REPORT, PRIMARY_FIGHT)]
    buffs = [_buffs_response()]  # primary: no Ebon Might received
    rankings_entries = []

    for i in range(n_clean + n_augmented):
        code = f"REFCODE{i:09d}"
        rankings_entries.append(
            {"name": f"Ref{i}", "duration": 100_000, "report": {"code": code, "fightID": 1}}
        )
        meta.append(
            _meta_response(
                player_id=100 + i,
                player_name=f"Ref{i}",
                class_name="Warlock",
                spec_name="Demonology",
                damage_total=900_000.0,
            )
        )
        events.append(
            _events_response(
                [{"sourceID": 100 + i, "type": "cast", "abilityGameID": 104316, "timestamp": 1300}]
            )
        )
        percentile.append(_percentile_response(None, code, 1))
        is_augmented = i >= n_clean
        buffs.append(_buffs_response([395152]) if is_augmented else _buffs_response())

    responses = {
        "meta": meta,
        "events": events,
        "percentile": percentile,
        "buffs": buffs,
        "rankings": [
            {
                "data": {
                    "worldData": {
                        "encounter": {
                            "characterRankings": {
                                "rankings": rankings_entries,
                                "hasMorePages": False,
                            }
                        }
                    }
                }
            }
        ],
        "partition": _zone_partitions_response(),
        "report_rankings": _report_rankings_response(partition=3),
    }
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)

    result = run_analysis(_req(), deps)

    assert result.header.matched_reference_n == n_clean
    assert result.header.reference_n == 0  # no reconciled damage in this fixture
    assert "has_augmentation" in result.header.matched_covariates
    assert "has_augmentation" not in result.header.relaxed_covariates


def test_relaxed_has_augmentation_shows_support_buff_warning_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T2.1 acceptance: when the only way to reach COHORT_MIN_HARD is to
    admit Augmentation-buffed candidates, the RENDERED report carries the
    support-buff warning.
    """
    monkeypatch.setattr(
        pipeline_module,
        "CohortCriteria",
        partial(pipeline_module.CohortCriteria, matching_policy_version="v1"),
    )
    n_clean = 3  # below COHORT_MIN_HARD alone — forces has_augmentation to relax
    n_augmented = 8
    meta = [_meta_response(class_name="Warlock", spec_name="Demonology")]
    primary_cast = {"sourceID": 6, "type": "cast", "abilityGameID": 104316, "timestamp": 1300}
    events = [_events_response([primary_cast])]
    percentile = [_percentile_response(71.0, PRIMARY_REPORT, PRIMARY_FIGHT)]
    buffs = [_buffs_response()]
    rankings_entries = []

    total = n_clean + n_augmented
    for i in range(total):
        code = f"REFCODE{i:09d}"
        rankings_entries.append(
            {"name": f"Ref{i}", "duration": 100_000, "report": {"code": code, "fightID": 1}}
        )
        meta.append(
            _meta_response(
                player_id=100 + i,
                player_name=f"Ref{i}",
                class_name="Warlock",
                spec_name="Demonology",
                damage_total=900_000.0,
            )
        )
        events.append(
            _events_response(
                [{"sourceID": 100 + i, "type": "cast", "abilityGameID": 104316, "timestamp": 1300}]
            )
        )
        percentile.append(_percentile_response(None, code, 1))
        is_augmented = i >= n_clean
        buffs.append(_buffs_response([395152]) if is_augmented else _buffs_response())

    responses = {
        "meta": meta,
        "events": events,
        "percentile": percentile,
        "buffs": buffs,
        "rankings": [
            {
                "data": {
                    "worldData": {
                        "encounter": {
                            "characterRankings": {
                                "rankings": rankings_entries,
                                "hasMorePages": False,
                            }
                        }
                    }
                }
            }
        ],
        "partition": _zone_partitions_response(),
        "report_rankings": _report_rankings_response(partition=3),
    }
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)

    result = run_analysis(_req(), deps)

    assert "has_augmentation" in result.header.relaxed_covariates
    text = render_report(result.header, result.comparisons, result.manifest)
    assert (
        "⚠️ Buffs de suporte não pareados — parte da diferença observada "
        "pode não ser controlável por você." in text
    )


# EC.4: `test_minority_build_player_gets_a_build_divergence_finding_end_to_end`
# (T2.2) was removed along with `AnalysisResult.build_divergence` — see
# analysis/talent_cluster.py's module docstring for why. A minority-build
# player's talents no longer produce a divergence finding from the
# execution pipeline at all; that observational question now belongs to
# Setup Analysis (SA.2's `compare_talent_build`), integrated into the
# report by a later milestone (RP.1/RP.2), not the execution pipeline.


def test_cold_build_persists_a_candidate_pool_for_reuse(tmp_path: Path) -> None:
    """T2.1 (docs/desvios.md D-25): the Store caches the raw candidate
    pool, not an aggregated profile — matching is per-player and always
    runs fresh (see analysis/cohort_match.py).
    """
    transport = _DispatchTransport(_happy_path_responses())
    deps = _build_deps(tmp_path, transport)

    result = run_analysis(_req(), deps)

    pool = deps.store.read_candidate_pool(result.manifest.cohort_id)
    assert pool is not None
    assert len(pool) == N_REFS


def test_second_call_with_a_warm_candidate_pool_makes_zero_ranking_queries(tmp_path: Path) -> None:
    """T1.7's own acceptance criterion, in spirit: once a candidate pool is
    cached, a second analysis for the same criteria never re-queries
    characterRankings — the warm path is fetch-the-user's-log +
    lookup-a-pool, not a fresh 100-log fetch.
    """
    transport = _DispatchTransport(_happy_path_responses())
    deps = _build_deps(tmp_path, transport)
    run_analysis(_req(), deps)  # cold build, populates the cache
    assert "rankings" in transport.calls
    transport.calls.clear()

    result = run_analysis(_req(), deps)

    assert "rankings" not in transport.calls
    assert result.header.matched_reference_n == N_REFS


def test_cohort_not_ready_when_cold_build_disallowed_and_nothing_cached(tmp_path: Path) -> None:
    responses = _happy_path_responses()
    del responses["rankings"]  # must never be needed — allow_cold_build=False
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)

    with pytest.raises(CohortNotReady):
        run_analysis(_req(), deps, allow_cold_build=False)

    assert "rankings" not in transport.calls


def test_allow_cold_build_false_still_uses_an_existing_warm_candidate_pool(tmp_path: Path) -> None:
    transport = _DispatchTransport(_happy_path_responses())
    deps = _build_deps(tmp_path, transport)
    run_analysis(_req(), deps)  # cold build (allowed), populates the cache
    transport.calls.clear()

    result = run_analysis(_req(), deps, allow_cold_build=False)

    assert "rankings" not in transport.calls
    assert result.header.matched_reference_n == N_REFS


def test_devourer_passes_scope_gate_offline(tmp_path: Path) -> None:
    transport = _DispatchTransport(
        _happy_path_responses(class_name="DemonHunter", spec_name="Devourer")
    )
    deps = _build_deps(tmp_path, transport)

    result = run_analysis(_req(), deps)

    assert result.header.class_name == "DemonHunter"
    assert result.header.spec == "Devourer"
    assert "rankings" in transport.calls


def test_scope_rejection_triggers_zero_ranking_queries(tmp_path: Path) -> None:
    """T0.9's scope gate must reject BEFORE any ranking query — a tank spec
    (Protection Warrior) never spends a cohort API point.
    """
    responses = _happy_path_responses(class_name="Warrior", spec_name="Protection")
    # No "rankings" fixture at all — if the gate lets a ranking query
    # through, the transport itself fails the test (see handle_request).
    del responses["rankings"]
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)
    req = _req()

    with pytest.raises(ScopeRejected, match="tanks"):
        run_analysis(req, deps)

    assert "rankings" not in transport.calls


def test_augmentation_evoker_rejected_with_correct_message(tmp_path: Path) -> None:
    responses = _happy_path_responses(class_name="Evoker", spec_name="Augmentation")
    del responses["rankings"]
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)
    req = _req()

    with pytest.raises(ScopeRejected, match="atribuída a outros"):
        run_analysis(req, deps)


def test_healer_spec_triggers_zero_ranking_queries(tmp_path: Path) -> None:
    responses = _happy_path_responses(class_name="Priest", spec_name="Discipline")
    del responses["rankings"]
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)

    with pytest.raises(ScopeRejected, match="healers"):
        run_analysis(_req(), deps)

    assert "rankings" not in transport.calls


def test_unknown_spec_triggers_zero_ranking_queries(tmp_path: Path) -> None:
    responses = _happy_path_responses(class_name="Mage", spec_name="Chronomancer")
    del responses["rankings"]
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)

    with pytest.raises(ScopeRejected, match="não reconhecida"):
        run_analysis(_req(), deps)

    assert "rankings" not in transport.calls


def test_player_not_found_propagates(tmp_path: Path) -> None:
    responses = {"meta": [_meta_response(no_player=True)], "events": [], "percentile": []}
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)
    req = _req("Nobody")

    with pytest.raises(PlayerNotFound):
        run_analysis(req, deps)


def test_insufficient_cohort_propagates(tmp_path: Path) -> None:
    responses = _happy_path_responses()
    responses["rankings"] = [_rankings_response(3)]  # below COHORT_MIN_HARD (8)
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)
    req = _req()

    with pytest.raises(InsufficientCohort):
        run_analysis(req, deps)


def test_insufficient_ledger_after_covariate_matching_completes_with_ledger_state_insufficient(
    tmp_path: Path,
) -> None:
    """T2.1 acceptance, updated by M2.3 §6.1: 8 raw candidates clear
    rankings.py's own ±35% gate (so InsufficientCohort is NOT raised there),
    but every one's own fight is 30s off the player's 100s fight — outside
    match_covariates' ±20% duration ceiling (max(100*0.20, 15)=20s), which
    is never relaxed further no matter how every other covariate degrades.
    difficulty/partition/class/spec are exact by construction (the rankings
    query itself) and are never touched by match_covariates either way.

    Before M2.3, pipeline.py raised InsufficientCohort from the post-
    matching count. M2.3 §6.1 removes that abort: the analysis now
    completes with ledger_state INSUFFICIENT_REFERENCES (§6.2) — the
    ledger-dependent consumers (performance, CD comparisons, core
    abilities/procs) are the ones that go empty/None, not the whole
    analysis.
    """
    meta = [_meta_response(class_name="Warlock", spec_name="Demonology")]
    primary_cast = {"sourceID": 6, "type": "cast", "abilityGameID": 104316, "timestamp": 1300}
    events = [_events_response([primary_cast])]
    percentile = [_percentile_response(71.0, PRIMARY_REPORT, PRIMARY_FIGHT)]
    rankings_entries = []
    for i in range(N_REFS):
        code = f"REFCODE{i:09d}"
        rankings_entries.append(
            {"name": f"Ref{i}", "duration": 130_000, "report": {"code": code, "fightID": 1}}
        )
        meta.append(
            _meta_response(
                player_id=100 + i,
                player_name=f"Ref{i}",
                class_name="Warlock",
                spec_name="Demonology",
                start=0,
                end=130_000,  # 130s: 30s off the 100s primary fight
                damage_total=900_000.0,
            )
        )
        events.append(
            _events_response(
                [{"sourceID": 100 + i, "type": "cast", "abilityGameID": 104316, "timestamp": 1300}]
            )
        )
        percentile.append(_percentile_response(None, code, 1))

    off_duration_rankings = {
        "data": {
            "worldData": {
                "encounter": {
                    "characterRankings": {"rankings": rankings_entries, "hasMorePages": False}
                }
            }
        }
    }
    responses = {
        "meta": meta,
        "events": events,
        "percentile": percentile,
        "rankings": [off_duration_rankings],
        "partition": _zone_partitions_response(),
        "report_rankings": _report_rankings_response(partition=3),
    }
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)
    req = _req()

    result = run_analysis(req, deps)

    assert result.matched_cohort_members == 0
    assert result.comparability is not None
    assert result.comparability.ledger.state == "INSUFFICIENT_REFERENCES"
    # §6.2's "não computado" column: ledger-dependent consumers go empty/None.
    assert result.performance is None
    assert result.core_abilities == ()
    assert result.proc_analysis is None
    assert result.external_dps_context == ()
    assert result.comparisons == ()
    # §6.2's "sempre computado" column: dps_gap and the six per-metric
    # comparisons stay available, independent of the ledger's own size.
    assert result.dps_gap is not None
    assert result.dps_gap.metric_comparisons != {}


# -- B1/B2: build frio interativo e incremental e resumivel ------------------------


def test_interactive_cold_build_defers_without_spending_a_single_construction_query(
    tmp_path: Path,
) -> None:
    """B2 — o caminho da fila precisa devolver um ADIAMENTO, não uma falha.

    `CohortNotReady`/`BotGitGudError` genérico levariam o worker a
    `mark_failed`, descartando o pedido do usuário. E um build adiado não pode
    gastar nem uma query de construção.
    """
    # available = 10000 - 8800 = 1200: cabe menos de uma referência, mas o teto
    # da conta comporta a política, então isto é um defer real, não uma
    # configuração impossível.
    transport = _DispatchTransport(_happy_path_responses(), points_spent=8800.0)
    deps = _build_deps(tmp_path, transport)

    with pytest.raises(CohortDeferredBudget) as caught:
        run_analysis(_req(), deps, allow_cold_build=True)

    assert caught.value.cohort_id
    assert "rankings" not in transport.calls
    assert deps.store.read_candidate_pool(caught.value.cohort_id) is None
    deps.store.close()


def test_interactive_cold_build_only_publishes_the_pool_once_every_log_is_cached(
    tmp_path: Path,
) -> None:
    """Parcial nunca é READY: o pool é o sinal de "coorte pronta", então
    escrevê-lo antes dos logs (como o caminho interativo fazia) publicava uma
    coorte cujos membros ainda custariam pontos.
    """
    transport = _DispatchTransport(_happy_path_responses())
    deps = _build_deps(tmp_path, transport)

    result = run_analysis(_req(), deps, allow_cold_build=True)

    cohort_id = result.manifest.cohort_id
    pool = deps.store.read_candidate_pool(cohort_id)
    assert pool is not None
    # Todo membro publicado ja esta em cache — nenhuma pendencia paga depois.
    for candidate in pool:
        assert (
            deps.store.read_log(candidate.report_code, candidate.fight_id, candidate.player_name)
            is not None
        )
    deps.store.close()


def test_a_second_analysis_of_a_ready_cohort_spends_no_reference_queries(
    tmp_path: Path,
) -> None:
    """Cache como checkpoint: a coorte pronta torna a próxima análise quente."""
    transport = _DispatchTransport(_happy_path_responses())
    deps = _build_deps(tmp_path, transport)
    run_analysis(_req(), deps, allow_cold_build=True)
    calls_after_cold = len(transport.calls)

    run_analysis(_req(), deps, allow_cold_build=True)

    # A segunda passagem re-resolve partition e o log do proprio jogador, mas
    # nao volta aos rankings nem aos logs de referencia.
    assert transport.calls.count("rankings") == 1
    assert len(transport.calls) < calls_after_cold * 2
    deps.store.close()


# -- M29: materiality/conclusion propagation on AnalysisResult --------------


def test_analysis_result_conclusion_and_positive_observation_default_to_none() -> None:
    """M29: `conclusion`/`positive_observation` are additive fields — an
    `AnalysisResult` built by hand (every non-`run_analysis` test in this
    file does this implicitly through `run_analysis`, but future callers
    might not) must never require them."""
    import dataclasses

    fields_by_name = {f.name: f for f in dataclasses.fields(pipeline_module.AnalysisResult)}
    assert fields_by_name["conclusion"].default is None
    assert fields_by_name["positive_observation"].default is None
    assert fields_by_name["material_priorities"].default == ()


def test_run_analysis_populates_conclusion_end_to_end(tmp_path: Path) -> None:
    """RB-4, wired through the real pipeline (not just materiality.py in
    isolation): `conclusion.standing` is graded against the PAIRED
    cohort's DPS values while `conclusion.percentile` carries the SAME
    WCL ranking percentile already on the header — two facts, kept
    separate, both reachable from one `run_analysis` call. Needs a cohort
    of at least MIN_N_FOR_GRADING (15) for `standing` to actually grade
    instead of `insufficient` — same reasoning as the M27 execution-
    findings non-vacuity test above.
    """
    n_refs = 20
    meta = [
        _meta_response(
            class_name="Warlock", spec_name="Demonology", combatant_info=_SHARED_COMBATANT_INFO
        )
    ]
    primary_cast = {"sourceID": 6, "type": "cast", "abilityGameID": 104316, "timestamp": 1300}
    events = [_events_response([primary_cast])]
    percentile = [_percentile_response(71.0, PRIMARY_REPORT, PRIMARY_FIGHT)]

    for i in range(n_refs):
        meta.append(
            _meta_response(
                player_id=100 + i,
                player_name=f"Ref{i}",
                class_name="Warlock",
                spec_name="Demonology",
                damage_total=900_000.0,
                combatant_info=_SHARED_COMBATANT_INFO,
            )
        )
        events.append(
            _events_response(
                [{"sourceID": 100 + i, "type": "cast", "abilityGameID": 104316, "timestamp": 1300}]
            )
        )
        percentile.append(_percentile_response(None, f"REFCODE{i:09d}", 1))

    responses: dict[str, Any] = {
        "meta": meta,
        "events": events,
        "percentile": percentile,
        "rankings": [_rankings_response(n_refs)],
        "partition": _zone_partitions_response(),
        "report_rankings": _report_rankings_response(partition=3),
    }
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)

    result = run_analysis(_req(), deps)

    assert result.matched_cohort_members is not None and result.matched_cohort_members >= 15
    assert result.conclusion is not None
    assert result.conclusion.standing is None
    assert result.conclusion.percentile == result.header.player_percentile
    assert result.conclusion.sample.matched_n == result.header.reference_n == 0
    assert result.conclusion.material_count >= 0
    assert isinstance(result.conclusion.material_count, int)
    assert isinstance(result.material_priorities, tuple)
    assert len(result.material_priorities) <= 3


def test_top_actions_unaffected_by_materiality_wiring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TEST_PLAN item 9 ("fixação user-facing"): `select_top_priorities`
    (findings.py — out of scope for this unit, RB-6) must still be called
    with EXACTLY the two arguments it always took, and its return value
    must still be exactly what `AnalysisResult.top_actions` carries. This
    is the guard that the new materiality/conclusion layer sits strictly
    AFTER that call, never as a filter in front of it.
    """
    from botgitgud.analysis.findings import select_top_priorities as real_select_top_priorities

    captured: dict[str, object] = {}

    def _spy(
        findings: object, relevance_findings: object, **kwargs: object
    ) -> pipeline_module.TopPriorities:
        captured["findings"] = list(findings)  # type: ignore[call-overload]
        captured["relevance_findings"] = list(relevance_findings)  # type: ignore[call-overload]
        captured["kwargs"] = kwargs
        return real_select_top_priorities(findings, relevance_findings, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline_module, "select_top_priorities", _spy)

    transport = _DispatchTransport(_happy_path_responses())
    deps = _build_deps(tmp_path, transport)

    result = run_analysis(_req(), deps)

    assert captured["kwargs"] == {}
    expected = real_select_top_priorities(
        captured["findings"],  # type: ignore[arg-type]
        captured["relevance_findings"],  # type: ignore[arg-type]
    )
    assert result.top_actions == expected
