"""A15/A19: additive migration, actual disk round-trips and historical absence."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from test_m1_astra_regressions import _log

from botgitgud.bot.analysis_runs import AnalysisRun, read_analysis_run, write_analysis_run
from botgitgud.domain.models import CollectionStatus, RunManifest
from botgitgud.ingest.parquet_codec import read_parquet_log, write_parquet_log
from botgitgud.ingest.store import Store


def test_a19_migrate_twice_preserves_historical_run_and_new_versions(tmp_path: Path) -> None:
    db = tmp_path / "warehouse.duckdb"
    with duckdb.connect(str(db)) as connection:
        connection.execute(
            "CREATE TABLE runs (cohort_id VARCHAR, code_version VARCHAR, "
            "generated_at TIMESTAMP, n_members INTEGER, wcl_partition INTEGER, "
            "settings_hash VARCHAR)"
        )
        connection.execute(
            "INSERT INTO runs VALUES ('old','old-code','2020-01-01',20,1,'old-settings')"
        )
    manifest = RunManifest(
        "new",
        "new-code",
        datetime.now(UTC),
        25,
        2,
        "new-settings",
        measurement_input_version="measurement-input-v1",
        damage_comparison_version="damage-comparison-v2",
        reference_n_quantitative=17,
    )
    for iteration in range(2):
        with Store(tmp_path) as store:
            old = store.query("SELECT * FROM runs WHERE cohort_id='old'").row(0)
            assert old == (
                "old",
                "old-code",
                datetime(2020, 1, 1),
                20,
                1,
                "old-settings",
                None,
                None,
                None,
            )
            if iteration == 0:
                store.write_run(manifest)
            assert store.query(
                "SELECT measurement_input_version,damage_comparison_version,"
                "reference_n_quantitative FROM runs WHERE cohort_id='new'"
            ).rows() == [("measurement-input-v1", "damage-comparison-v2", 17)]


@pytest.mark.parametrize("status", list(CollectionStatus))
def test_a19_new_provenance_round_trip(status: CollectionStatus, tmp_path: Path) -> None:
    log = _log(100, damage_status=status, casts_status=status)
    path = tmp_path / "new.parquet"
    write_parquet_log(log, path)
    before = path.read_bytes()
    result = read_parquet_log(path)
    assert result.measurement_provenance == log.measurement_provenance
    assert result.damage_by_ability == log.damage_by_ability
    assert path.read_bytes() == before


def test_a15_legacy_absence_is_not_upgraded_on_read(tmp_path: Path) -> None:
    log = replace(_log(100), measurement_provenance=None)
    path = tmp_path / "legacy.parquet"
    write_parquet_log(log, path)
    before = path.read_bytes()
    assert read_parquet_log(path).measurement_provenance is None
    assert read_parquet_log(path).measurement_provenance is None
    assert path.read_bytes() == before


def test_a19_telemetry_versions_reach_disk(tmp_path: Path) -> None:
    run = AnalysisRun(
        analysis_id="m1",
        started_at="2026-09-12T00:00:00Z",
        measurement_input_version="measurement-input-v1",
        damage_comparison_version="damage-comparison-v2",
        reference_n_quantitative=17,
    )
    write_analysis_run(tmp_path, run)
    payload = read_analysis_run(tmp_path, "m1")
    assert payload is not None
    assert payload["measurement_input_version"] == "measurement-input-v1"
    assert payload["damage_comparison_version"] == "damage-comparison-v2"
    assert payload["reference_n_quantitative"] == 17
