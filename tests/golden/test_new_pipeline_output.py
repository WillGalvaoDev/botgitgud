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
    return render_report(result.header, result.comparisons)


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
