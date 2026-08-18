"""tests/fixtures/record.py — records live WCL/Blizzard HTTP traffic as cassettes.

Run manually whenever the fixture log needs to be (re)captured:

    python tests/fixtures/record.py

NEVER invoked by pytest — this is the writer side; tests/conftest.py's
`mock_http` fixture is the reader side (see http_cassette.py for the shared
key format both sides must agree on). Requires real WCL/Blizzard credentials
in .env at the repo root.

Runs the legacy pipeline in an isolated CWD (tests/fixtures/legacy_runner.py)
so it never mutates the git-tracked spells.json (docs/desvios.md D-4). The
new pipeline (T1.6+) needs no such isolation — every path is passed
explicitly to Deps rather than resolved bot.py-relative.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from http_cassette import Cassette, redact_headers, save_cassette
from httpx_cassette_transport import RecordingTransport
from legacy_runner import isolated_legacy_bot

from botgitgud.analysis.cohort import within_sanity_band
from botgitgud.analysis.comparison import compare_all_spells
from botgitgud.analysis.profile import build_cd_reference_profile, discover_eligible_spell_ids
from botgitgud.blizzard.client import BlizzardClient, BlizzardClientConfig
from botgitgud.domain.specs import SpecId, classify_spec
from botgitgud.domain.spells import SpellCatalog
from botgitgud.errors import BotGitGudError
from botgitgud.ingest.log_fetcher import LogFetcher
from botgitgud.ingest.rankings import RankingCandidate, fetch_cohort_logs, get_current_partition
from botgitgud.ingest.store import Store
from botgitgud.wcl.client import API_URL, WclClient, WclClientConfig
from botgitgud.wcl.queries import QUERY_RANKINGS_PAGE

FIXTURE_REPORT_CODE = "PtfBbQKRY9d6zAMC"
FIXTURE_FIGHT_ID = 1
FIXTURE_CHARACTER = "Zarad"
MAX_REFERENCE_LOGS = 5  # keep cassette count small; see T0.2 instructions
# T1.6/docs/desvios.md D-15: the live characterRankings pool for this
# encounter/spec has grown to ~99 sanity-band candidates (vs. the ~26
# T0.1 originally measured) — recording the new pipeline's full cohort
# would mean hundreds of MB of cassettes (each reference player's own
# "events" cassette is the whole raid's unfiltered cast log for the fight
# window, ~1MB+ apiece). The rankings cassette itself is truncated to
# this many candidates after the real call (see
# _fetch_and_truncate_rankings) — kept just above COHORT_MIN_HARD (8) so
# a stray per-candidate fetch failure doesn't tip the golden test into
# InsufficientCohort.
N_RECORDING_REFS = 24
# T2.1 (docs/desvios.md D-25): analysis/cohort_match.py's own duration
# cascade tops out at ±20%, tighter than this truncation's ±35%
# SANITY_BAND_PCT — a candidate outside ±20% is NEVER admitted by
# match_cohort, no matter how far every other covariate relaxes. Recording
# with the old N_RECORDING_REFS=10 empirically left only 4/10 candidates
# within ±20% (the other 6 sat in the 20-35% dead zone) — below
# COHORT_MIN_HARD (8). Widening the page-1 pull to 24 (~40% of this
# fixture's candidates land within ±20%, observed empirically) gives
# enough margin without adding pagination to this recording pass.

RECORDING_SCRATCH_DIR = Path(__file__).resolve().parent / "_record_scratch"
RECORDING_SCRATCH_DIR_NEW = Path(__file__).resolve().parent / "_record_scratch_new"
REPO_SPELLS_JSON = Path(__file__).resolve().parents[2] / "spells.json"


class _RecordingHooks:
    """Wraps requests.post/get, saving every real response as a cassette."""

    def __init__(self) -> None:
        self._real_post = requests.post
        self._real_get = requests.get
        self.recorded = 0

    def install(self) -> None:
        requests.post = self._post  # type: ignore[method-assign]
        requests.get = self._get  # type: ignore[method-assign]

    def uninstall(self) -> None:
        requests.post = self._real_post  # type: ignore[method-assign]
        requests.get = self._real_get  # type: ignore[method-assign]

    def _record(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None,
        headers: dict[str, str] | None,
        response: requests.Response,
    ) -> None:
        try:
            body = response.json()
        except ValueError:
            body = None
        path = save_cassette(
            Cassette(
                method=method,
                url=url,
                request_payload=payload,
                request_headers=redact_headers(headers),
                status_code=response.status_code,
                response_json=body,
            )
        )
        self.recorded += 1
        print(f"  gravado: {path.name}  ({method} {url})")

    def _post(
        self,
        url: str,
        *,
        data: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        timeout: float | None = None,
        **kw: Any,
    ) -> requests.Response:
        response = self._real_post(
            url, data=data, json=json, headers=headers, auth=auth, timeout=timeout, **kw
        )
        self._record("POST", url, json if json is not None else data, headers, response)
        return response

    def _get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        **kw: Any,
    ) -> requests.Response:
        response = self._real_get(url, headers=headers, params=params, timeout=timeout, **kw)
        self._record("GET", url, params, headers, response)
        return response


_MAX_RECORDING_PAGES = 10  # mirrors analysis/cohort.py's MAX_RANKING_PAGES


def _fetch_and_truncate_rankings(
    client: WclClient,
    *,
    encounter_id: int,
    class_name: str,
    spec_name: str,
    partition: int,
    target_duration_s: float,
) -> list[RankingCandidate]:
    """Pages through the real characterRankings call (RecordingTransport
    saves each page as a cassette) exactly like ingest/rankings.py's own
    fetch_ranking_candidates does, then overwrites each visited page's
    cassette (same request => same key, see http_cassette.cassette_key)
    with a version truncated to only the within-sanity-band candidates —
    see N_RECORDING_REFS's docstring for why. The last page visited gets
    hasMorePages forced to False, so the golden test's own replay (which
    re-runs this exact pagination independently) stops exactly where this
    recording pass did, on the same final candidate set.

    docs/desvios.md D-25: this fixture's live pool turned out to be thin
    enough (this partition is early in its season) that a single page no
    longer reliably yields N_RECORDING_REFS candidates — hence the
    pagination, mirroring production instead of assuming page 1 suffices.
    """
    candidates: list[RankingCandidate] = []
    page = 1
    while page <= _MAX_RECORDING_PAGES and len(candidates) < N_RECORDING_REFS:
        variables = {
            "encounterID": encounter_id,
            "className": class_name,
            "specName": spec_name,
            "page": page,
            "partition": partition,
        }
        res_json = client.query(QUERY_RANKINGS_PAGE, variables, op_name="fetch_rankings_page")
        rankings_data = (
            res_json.get("data", {})
            .get("worldData", {})
            .get("encounter", {})
            .get("characterRankings", {})
        )
        rankings_list = rankings_data.get("rankings", [])
        real_has_more = rankings_data.get("hasMorePages", False)

        kept_raw: list[dict[str, Any]] = []
        for r in rankings_list:
            dur_s = r.get("duration", 0) / 1000.0
            if not within_sanity_band(dur_s, target_duration_s):
                continue
            rep = r.get("report", {})
            if not (rep.get("code") and rep.get("fightID") is not None):
                continue
            kept_raw.append(r)
            candidates.append(RankingCandidate(rep["code"], rep["fightID"], r["name"], dur_s))
            if len(candidates) >= N_RECORDING_REFS:
                break

        stopping_here = len(candidates) >= N_RECORDING_REFS or not real_has_more
        truncated_response = {
            "data": {
                "worldData": {
                    "encounter": {
                        "characterRankings": {
                            "rankings": kept_raw,
                            "hasMorePages": not stopping_here,
                        }
                    }
                }
            }
        }
        save_cassette(
            Cassette(
                method="POST",
                url=API_URL,
                request_payload={"query": QUERY_RANKINGS_PAGE, "variables": variables},
                request_headers={},
                status_code=200,
                response_json=truncated_response,
            )
        )
        if stopping_here:
            break
        page += 1

    return candidates


_MAX_RECORDED_EVENTS_PER_PAGE = 25  # T3.1: see _truncate_non_fixture_event_cassettes' docstring


def _truncate_non_fixture_event_cassettes() -> int:
    """T3.1: `events(dataType: DamageDone)` pages at limit:10000 — full
    fidelity for every one of ~24 reference logs (many pet-heavy, same
    class/spec as the fixture) would balloon the cassette directory by
    hundreds of MB for data no test actually asserts on (the mandated
    reconciliation test — damage_by_ability summing to entry.total within
    1% — only needs FIXTURE_CHARACTER's own log to be exact). Same
    truncate-after-recording precedent as
    _fetch_and_truncate_rankings: overwrite each non-fixture
    DamageDone/Resources events cassette in place (same request => same
    key) with its first `_MAX_RECORDED_EVENTS_PER_PAGE` events and
    `nextPageTimestamp: null`, so replay stops pagination immediately
    without erroring. Only touches cassettes for a *different*
    report_code/fightID than the fixture's own — Zarad's own damage/
    resource event cassettes are left byte-for-byte as recorded.
    """
    from http_cassette import CASSETTES_DIR, load_cassette, save_cassette

    truncated = 0
    for path in CASSETTES_DIR.glob("*.json"):
        cassette = load_cassette(path.stem)
        if cassette is None:
            continue
        query = (cassette.request_payload or {}).get("query", "")
        if "GetPlayerDamageEvents" not in query and "GetPlayerResourceEvents" not in query:
            continue
        variables = (cassette.request_payload or {}).get("variables", {})
        if variables.get("code") == FIXTURE_REPORT_CODE and variables.get("fightIDs") == [
            FIXTURE_FIGHT_ID
        ]:
            continue  # the fixture's own log stays exact, for the reconciliation test

        report = (
            (cassette.response_json or {}).get("data", {}).get("reportData", {}).get("report", {})
        )
        events = report.get("events")
        if not isinstance(events, dict) or not events.get("data"):
            continue
        events["data"] = events["data"][:_MAX_RECORDED_EVENTS_PER_PAGE]
        events["nextPageTimestamp"] = None
        save_cassette(cassette)
        truncated += 1
    return truncated


def _record_new_pipeline() -> int:
    """T1.6: the new pipeline (analysis/pipeline.py) uses httpx
    (WclClient/BlizzardClient), so it needs its own recording pass with an
    httpx-flavored transport (see httpx_cassette_transport.py) — the
    requests-based _RecordingHooks above is blind to httpx traffic.

    Steps are inlined here rather than calling run_analysis() directly so
    the reference-cohort candidate list can be truncated before the full
    per-candidate log fetch (see _fetch_and_truncate_rankings).

    No CWD isolation needed (unlike the old bot.py-based version): every
    path is passed explicitly rather than resolved bot.py-relative.
    spells.json is still copied from the repo root first so the catalog
    starts pre-seeded, same as the golden test that replays these
    cassettes (tests/golden/test_new_pipeline_output.py) — otherwise this
    recording pass and that replay would disagree on which spells need a
    Blizzard fallback call.
    """
    # T3.1: a stale Store from a previous recording pass makes fetcher.fetch()
    # a cache hit for every reference log — zero new network calls, so a
    # query-shape change (e.g. this task's new QUERY_PLAYER_META fields)
    # would silently record NOTHING for them. Wipe it every run.
    if (RECORDING_SCRATCH_DIR_NEW / "data").exists():
        shutil.rmtree(RECORDING_SCRATCH_DIR_NEW / "data")
    RECORDING_SCRATCH_DIR_NEW.mkdir(parents=True, exist_ok=True)
    isolated_spells = RECORDING_SCRATCH_DIR_NEW / "spells.json"
    if REPO_SPELLS_JSON.exists():
        shutil.copy(REPO_SPELLS_JSON, isolated_spells)

    wcl_client_id = ""
    wcl_client_secret = ""
    blizzard_client_id = ""
    blizzard_client_secret = ""
    with isolated_legacy_bot(RECORDING_SCRATCH_DIR) as legacy_bot:
        wcl_client_id = legacy_bot.WCL_CLIENT_ID or ""
        wcl_client_secret = legacy_bot.WCL_CLIENT_SECRET or ""
        blizzard_client_id = legacy_bot.BLIZZARD_CLIENT_ID or ""
        blizzard_client_secret = legacy_bot.BLIZZARD_CLIENT_SECRET or ""

    wcl_transport = RecordingTransport()
    client = WclClient(
        WclClientConfig(client_id=wcl_client_id, client_secret=wcl_client_secret),
        transport=wcl_transport,
    )
    blizzard_transport = RecordingTransport()
    blizzard = BlizzardClient(
        BlizzardClientConfig(client_id=blizzard_client_id, client_secret=blizzard_client_secret),
        transport=blizzard_transport,
    )
    catalog = SpellCatalog(isolated_spells, blizzard=blizzard)
    store = Store(RECORDING_SCRATCH_DIR_NEW / "data")
    fetcher = LogFetcher(client, store, catalog)

    try:
        player_log = fetcher.fetch(FIXTURE_REPORT_CODE, FIXTURE_FIGHT_ID, FIXTURE_CHARACTER)

        spec_id = SpecId(
            class_name=player_log.build.class_name, spec_name=player_log.build.spec_name
        )
        if classify_spec(spec_id).value != "supported":
            print(f"ERRO: personagem de fixture fora de escopo ({spec_id})")
            return 1

        partition = get_current_partition(client, player_log.fight.encounter_id)
        candidates = _fetch_and_truncate_rankings(
            client,
            encounter_id=player_log.fight.encounter_id,
            class_name=player_log.build.class_name,
            spec_name=player_log.build.spec_name,
            partition=partition,
            target_duration_s=player_log.fight.duration_s,
        )
        reference_logs = fetch_cohort_logs(fetcher, candidates, max_workers=5)

        profile, num_positional = build_cd_reference_profile(
            reference_logs, player_log.fight.duration_s
        )
        eligible_ids = discover_eligible_spell_ids(profile)
        compare_all_spells(
            player_log, profile, eligible_ids, catalog=catalog, reference_n=num_positional
        )
        catalog.flush()
    except BotGitGudError as e:
        print(f"ERRO: pipeline novo — {e}")
        return 1
    finally:
        store.close()
        client.close()
        blizzard.close()

    total = wcl_transport.recorded + blizzard_transport.recorded
    print(f"\nPipeline novo: {total} cassetes adicionais gravados/confirmados.")

    n_truncated = _truncate_non_fixture_event_cassettes()
    print(f"T3.1: {n_truncated} cassete(s) de eventos de referência truncados pós-gravação.")
    return 0


def main() -> int:
    hooks = _RecordingHooks()
    with isolated_legacy_bot(RECORDING_SCRATCH_DIR) as legacy_bot:
        hooks.install()
        try:
            token = legacy_bot.get_wcl_token()
            if not token:
                print("ERRO: falha ao obter token WCL — verifique .env na raiz do repo")
                return 1

            user_data = legacy_bot.fetch_player_timeline_data(
                token, FIXTURE_REPORT_CODE, FIXTURE_FIGHT_ID, FIXTURE_CHARACTER
            )
            if not user_data:
                print(f"ERRO: jogador '{FIXTURE_CHARACTER}' não encontrado no fight de fixture")
                return 1

            references, matched, min_d, max_d, avg_p, _parses, min_p, max_p = (
                legacy_bot.fetch_top_logs_for_cds(
                    token,
                    user_data["fight"]["encounter_id"],
                    user_data["build"]["class"],
                    user_data["build"]["spec"],
                    user_data["fight"]["duration_sec"],
                )
            )
            print(f"\nReferências capturadas: {matched}")
            if matched > MAX_REFERENCE_LOGS:
                print(
                    f"AVISO: {matched} referências > limite documentado de {MAX_REFERENCE_LOGS}. "
                    "O pool real de rankings para este encontro/spec é pequeno (ver "
                    "docs/schema_confirmado.md §8), então isso não era esperado — investigue "
                    "antes de prosseguir; não edite este script para simplesmente elevar o limite."
                )

            # Espelha o pipeline COMPLETO (igual a cmd_analisar/process_analysis em
            # legacy/bot.py), não só até fetch_top_logs_for_cds: build_cd_reference_profile
            # chama get_spell_data() por spell_id, que pode cair no fallback da API da
            # Blizzard para spells ausentes do castsTable do WCL — essas chamadas também
            # precisam de cassete, senão o golden test falha ao rodar o pipeline inteiro.
            profile = legacy_bot.build_cd_reference_profile(references)
            major_cds = legacy_bot.discover_clean_major_cds(profile)
            comparison = legacy_bot.compare_major_cds_clean(user_data, profile, major_cds)
            legacy_bot.generate_coach_report_string(
                FIXTURE_CHARACTER, user_data, matched, min_d, max_d, avg_p, min_p, max_p, comparison
            )
        finally:
            hooks.uninstall()

    print(f"\n{hooks.recorded} cassetes gravados em tests/fixtures/cassettes/")

    new_pipeline_result = _record_new_pipeline()
    if new_pipeline_result != 0:
        return new_pipeline_result

    cassettes_dir = Path(__file__).resolve().parent / "cassettes"
    # "Bearer " catches leaked request headers; "eyJ" catches a raw JWT access_token
    # that slipped into a response body without going through redact_response_body
    # (see docs/desvios.md D-6 — the OAuth token endpoint returns the real credential
    # in its JSON body, not just in a header).
    leak_markers = ("Bearer ", "eyJ")
    leaked = [
        f
        for f in cassettes_dir.glob("*.json")
        if any(marker in f.read_text(encoding="utf-8") for marker in leak_markers)
    ]
    if leaked:
        print(f"ERRO DE SEGURANÇA: segredo vazado em {[f.name for f in leaked]}")
        return 1

    print("Verificação de segurança OK: nenhum cassete contém segredos ('Bearer '/'eyJ').")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
