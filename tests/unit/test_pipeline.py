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
from test_log_fetcher import _events_response, _meta_response, _percentile_response

from botgitgud.analysis.pipeline import AnalysisRequest, Deps, run_analysis
from botgitgud.config import Settings
from botgitgud.domain.spells import SpellCatalog
from botgitgud.errors import InsufficientCohort, PlayerNotFound, ScopeRejected
from botgitgud.ingest.log_fetcher import LogFetcher
from botgitgud.ingest.store import Store
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
        elif "GetPlayerEvents" in query:
            op = "events"
        elif "GetPercentile" in query:
            op = "percentile"
        elif "GetRankingsCDs" in query:
            op = "rankings"
        else:
            pytest.fail(f"query GraphQL não reconhecida: {query[:80]}")

        if op not in self._responses:
            pytest.fail(f"operação '{op}' inesperada — nenhuma resposta canned para ela")

        self.calls.append(op)
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


def _happy_path_responses(
    *, class_name: str = "Warlock", spec_name: str = "Demonology"
) -> dict[str, Any]:
    meta = [_meta_response(class_name=class_name, spec_name=spec_name)]
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
