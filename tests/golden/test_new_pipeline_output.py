"""T1.6 — golden test for the new (Deps/pipeline-based) analysis output,
replacing the T0.7 version that loaded the now-deleted root bot.py by
file path. Same fixture log, same httpx ReplayTransport, same T0.7
acceptance criterion: the new report differs from the frozen legacy
snapshot, and that difference includes the USOS PERDIDOS section.

Unlike the old new_bot_runner.py-based version, this needs no CWD
isolation: every path (SpellCatalog, Store) is passed explicitly to Deps
rather than resolved as a bot.py-relative default (T1.6's whole point).
The repo's spells.json is copied into tmp_path so the catalog starts
pre-seeded exactly as it was when tests/fixtures/record.py captured these
cassettes — otherwise the pipeline would hit Blizzard fallback calls that
were never recorded, and ReplayTransport would fail the test outright.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from httpx_cassette_transport import ReplayTransport

from botgitgud.analysis.pipeline import AnalysisRequest, Deps, run_analysis
from botgitgud.blizzard.client import BlizzardClient, BlizzardClientConfig
from botgitgud.config import Settings
from botgitgud.domain.spells import SpellCatalog
from botgitgud.ingest.log_fetcher import LogFetcher
from botgitgud.ingest.store import Store
from botgitgud.report.text import render_report
from botgitgud.wcl.client import WclClient, WclClientConfig

FIXTURE_REPORT_CODE = "PtfBbQKRY9d6zAMC"
FIXTURE_FIGHT_ID = 1
FIXTURE_CHARACTER = "Zarad"

REPO_SPELLS_JSON = Path(__file__).resolve().parents[2] / "spells.json"


def _build_deps(tmp_path: Path) -> Deps:
    isolated_spells = tmp_path / "spells.json"
    if REPO_SPELLS_JSON.exists():
        shutil.copy(REPO_SPELLS_JSON, isolated_spells)

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        discord_token="d" * 10,
        wcl_client_id="test-id",
        wcl_client_secret="test-secret",
        blizzard_client_id="test-id",
        blizzard_client_secret="test-secret",
        # Historical cassette budget is not production state. Keep this
        # analytical golden focused on output equivalence; budget policy has
        # dedicated deterministic tests in test_cold_build.py.
        cold_build_points_per_query=0.5,
    )
    client = WclClient(
        WclClientConfig(client_id="test-id", client_secret="test-secret"),
        transport=ReplayTransport(),
    )
    blizzard = BlizzardClient(
        BlizzardClientConfig(client_id="test-id", client_secret="test-secret"),
        transport=ReplayTransport(),
    )
    catalog = SpellCatalog(isolated_spells, blizzard=blizzard)
    store = Store(tmp_path / "data")
    fetcher = LogFetcher(client, store, catalog)
    return Deps(client=client, fetcher=fetcher, store=store, catalog=catalog, settings=settings)


def _run_new_pipeline(tmp_path: Path) -> str:
    deps = _build_deps(tmp_path)
    req = AnalysisRequest(
        report_code=FIXTURE_REPORT_CODE, fight_id=FIXTURE_FIGHT_ID, character_name=FIXTURE_CHARACTER
    )
    result = run_analysis(req, deps)
    # manifest is deliberately omitted (None): RunManifest.generated_at/
    # code_version are wall-clock/git-HEAD dependent — including it would
    # make this a flaky snapshot, not a golden one. It has its own
    # dedicated (non-snapshot) coverage in test_runmanifest.py.
    return render_report(
        result.header,
        result.comparisons,
        None,
        result.build_divergence,
        result.performance,
        result.dps_gap,
        result.top_actions,
    )


def test_new_pipeline_report_matches_golden_snapshot(tmp_path: Path, snapshot: Any) -> None:
    report_text = _run_new_pipeline(tmp_path)
    assert report_text == snapshot


def test_new_pipeline_differs_from_legacy_and_shows_missed_usage(tmp_path: Path) -> None:
    """Critério de aceite da T0.7 (ainda válido pós-refatoração da T1.6):
    'o novo snapshot difere do da T0.2 e a diferença contém a seção
    USOS PERDIDOS'.
    """
    legacy_snapshot_path = (
        Path(__file__).resolve().parent / "__snapshots__" / "test_legacy_output.ambr"
    )
    legacy_text = legacy_snapshot_path.read_text(encoding="utf-8")

    report_text = _run_new_pipeline(tmp_path)

    assert report_text != legacy_text
    assert "USOS PERDIDOS" in report_text
    assert "USOS PERDIDOS" not in legacy_text


def test_new_pipeline_never_fabricates_parse_med(tmp_path: Path) -> None:
    """achado 3.10: o campo antigo 'Parse méd: 99' nunca deve reaparecer."""
    report_text = _run_new_pipeline(tmp_path)
    assert "Parse méd" not in report_text


def test_zarad_fixture_produces_exactly_five_phase_intervals(tmp_path: Path) -> None:
    """T2.4 acceptance, against the real fixture end-to-end (not just the
    hardcoded data in test_phases.py): the Zarad fight produces exactly 5
    intervals, with keys (1,0), (2,0), (1,1), (2,1), (1,2) — verified live,
    docs/schema_confirmado.md §7.
    """
    deps = _build_deps(tmp_path)
    player_log = deps.fetcher.fetch(FIXTURE_REPORT_CODE, FIXTURE_FIGHT_ID, FIXTURE_CHARACTER)

    assert [iv.key for iv in player_log.fight.phase_intervals] == [
        (1, 0),
        (2, 0),
        (1, 1),
        (2, 1),
        (1, 2),
    ]


def test_zarad_fixture_damage_by_ability_reconciles_with_authoritative_total(
    tmp_path: Path,
) -> None:
    """T3.1's mandated reconciliation test: aggregating raw DamageDone
    events by ability, restricted to {player} union {pets}
    (ingest/damage_aggregation.py), must reproduce the authoritative total
    within 1% — docs/schema_confirmado.md §5, live-verified for this exact
    fixture at 37.378.119 (player + 20 pets), 0.00% error. `damage_total`
    is reconstructed from `dps * duration_s` (PlayerLog carries no raw
    total field of its own) — the same Summary.damageDone.total §5 already
    confirms is identical to the DamageDone table's `entry.total`.
    """
    deps = _build_deps(tmp_path)
    player_log = deps.fetcher.fetch(FIXTURE_REPORT_CODE, FIXTURE_FIGHT_ID, FIXTURE_CHARACTER)

    assert player_log.dps is not None
    authoritative_total = player_log.dps * player_log.fight.duration_s
    aggregated_total = sum(ab.total for ab in player_log.damage_by_ability.values())

    assert authoritative_total > 0
    relative_error = abs(aggregated_total - authoritative_total) / authoritative_total
    assert relative_error < 0.01, (
        f"aggregated={aggregated_total} authoritative={authoritative_total} "
        f"error={relative_error:.4%}"
    )
