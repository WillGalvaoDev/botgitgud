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

from botgitgud.analysis.pipeline import AnalysisRequest, Deps, run_analysis
from botgitgud.config import Settings
from botgitgud.domain.spells import SpellCatalog
from botgitgud.errors import CohortNotReady, InsufficientCohort, PlayerNotFound, ScopeRejected
from botgitgud.ingest.log_fetcher import LogFetcher
from botgitgud.ingest.store import Store
from botgitgud.report.text import render_report
from botgitgud.wcl.client import WclClient, WclClientConfig

PRIMARY_REPORT = "ABCDEFGHIJKLMNOP"
PRIMARY_FIGHT = 1
N_REFS = 8  # COHORT_MIN_HARD


def _rate_limit_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": {
                "rateLimitData": {
                    "limitPerHour": 3600,
                    "pointsSpentThisHour": 0,
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
    def __init__(self, responses: dict[str, list[dict[str, Any]] | dict[str, Any]]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if "oauth.battle.net" in str(request.url) or "warcraftlogs.com/oauth" in str(request.url):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})

        body = json.loads(request.content)
        query = body.get("query", "")
        if "rateLimitData" in query:
            return _rate_limit_response()
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
    }


def test_run_analysis_happy_path_returns_header_and_comparisons(tmp_path: Path) -> None:
    transport = _DispatchTransport(_happy_path_responses())
    deps = _build_deps(tmp_path, transport)
    req = _req()

    result = run_analysis(req, deps)

    assert result.header.char_name == "Zarad"
    assert result.header.class_name == "Warlock"
    assert result.header.spec == "Demonology"
    assert result.header.reference_n == N_REFS
    assert result.header.player_dps == pytest.approx(10000.0)  # 1_000_000 / 100s
    assert len(result.comparisons) >= 1
    assert any(c.spell.spell_id == 104316 for c in result.comparisons)
    assert result.manifest.cohort_id
    assert result.manifest.wcl_partition == 3
    assert "has_augmentation" in result.header.matched_covariates
    assert "item_level" in result.header.matched_covariates


def test_player_without_augmentation_gets_an_augmentation_free_cohort(tmp_path: Path) -> None:
    """T2.1 acceptance: a player without Augmentation in the raid gets a
    cohort of ONLY non-Augmentation logs, as long as n >= COHORT_MIN_HARD
    without needing to relax has_augmentation — 8 clean candidates plus 4
    Augmentation-buffed ones are offered; only the 8 clean ones survive.
    """
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
    }
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)

    result = run_analysis(_req(), deps)

    assert result.header.reference_n == n_clean
    assert "has_augmentation" in result.header.matched_covariates
    assert "has_augmentation" not in result.header.relaxed_covariates


def test_relaxed_has_augmentation_shows_support_buff_warning_end_to_end(tmp_path: Path) -> None:
    """T2.1 acceptance: when the only way to reach COHORT_MIN_HARD is to
    admit Augmentation-buffed candidates, the RENDERED report carries the
    support-buff warning.
    """
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
    }
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)

    result = run_analysis(_req(), deps)

    assert "has_augmentation" in result.header.relaxed_covariates
    text = render_report(result.header, result.comparisons, result.manifest)
    assert (
        "⚠️ Buffs de suporte não pareados — parte do gap de dano por cast "
        "pode não ser controlável por você." in text
    )


def test_minority_build_player_gets_a_build_divergence_finding_end_to_end(tmp_path: Path) -> None:
    """T2.2 acceptance: a player in a minority talent cluster gets the
    BUILD DIVERGENTE finding, and it opens the rendered report before any
    timing analysis.
    """
    dominant_talents: dict[str, Any] = {
        "talentTree": [{"id": 1, "rank": 1, "nodeID": 100}],
        "gear": [],
    }
    minority_talents: dict[str, Any] = {
        "talentTree": [{"id": 2, "rank": 2, "nodeID": 200}],
        "gear": [],
    }
    n_dominant = 9
    n_minority_cohort = 1  # + the target itself -> minority cluster of 2

    meta = [
        _meta_response(
            class_name="Warlock", spec_name="Demonology", combatant_info=minority_talents
        )
    ]
    primary_cast = {"sourceID": 6, "type": "cast", "abilityGameID": 104316, "timestamp": 1300}
    events = [_events_response([primary_cast])]
    percentile = [_percentile_response(71.0, PRIMARY_REPORT, PRIMARY_FIGHT)]
    buffs = [_buffs_response()]
    rankings_entries = []

    total = n_dominant + n_minority_cohort
    for i in range(total):
        code = f"REFCODE{i:09d}"
        rankings_entries.append(
            {"name": f"Ref{i}", "duration": 100_000, "report": {"code": code, "fightID": 1}}
        )
        is_minority = i >= n_dominant
        meta.append(
            _meta_response(
                player_id=100 + i,
                player_name=f"Ref{i}",
                class_name="Warlock",
                spec_name="Demonology",
                damage_total=900_000.0,
                combatant_info=minority_talents if is_minority else dominant_talents,
            )
        )
        events.append(
            _events_response(
                [{"sourceID": 100 + i, "type": "cast", "abilityGameID": 104316, "timestamp": 1300}]
            )
        )
        percentile.append(_percentile_response(None, code, 1))
        buffs.append(_buffs_response())

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
    }
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)

    result = run_analysis(_req(), deps)

    assert result.build_divergence is not None
    assert result.build_divergence.player_cluster_n == 2
    assert result.build_divergence.dominant_cluster_n == n_dominant
    text = render_report(
        result.header, result.comparisons, result.manifest, result.build_divergence
    )
    # T3.3: BUILD DIVERGENTE now renders as the first "detalhamento por
    # categoria" item, after the header/Top3/DPS-gap sections — no longer
    # literally the first line of the report (that was T2.2's provisional
    # placement, pending this task).
    assert "BUILD DIVERGENTE" in text
    assert text.index("GITGUD MAJOR CD ANALYSIS") < text.index("BUILD DIVERGENTE")


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
    assert result.header.reference_n == N_REFS


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
    assert result.header.reference_n == N_REFS


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


def test_insufficient_cohort_after_covariate_matching_never_relaxes_difficulty(
    tmp_path: Path,
) -> None:
    """T2.1 acceptance: 8 raw candidates clear rankings.py's own ±35% gate
    (so InsufficientCohort is NOT raised there), but every one's own fight
    is 30s off the player's 100s fight — outside match_cohort's ±20%
    duration ceiling (max(100*0.20, 15)=20s), which is never relaxed
    further no matter how every other covariate degrades.
    difficulty/partition/class/spec are exact by construction (the
    rankings query itself) and are never touched by match_cohort either
    way — pipeline.py must still raise InsufficientCohort from the
    post-matching count.
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
    }
    transport = _DispatchTransport(responses)
    deps = _build_deps(tmp_path, transport)
    req = _req()

    with pytest.raises(InsufficientCohort):
        run_analysis(req, deps)
