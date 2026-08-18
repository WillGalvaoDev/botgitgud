"""T1.3 — DuckDB + Parquet storage layer.

See docs/desvios.md D-12 for four gaps in this task's own spec (FightRef
missing `partition`, `CohortProfile` never defined anywhere, a PRIMARY KEY
that contradicts the "raw data is immutable" principle stated as
obligatory in the same section, and a parquet path template that collides
across players and re-ingestions) and how each was resolved.

Layout:
    data/
    ├── warehouse.duckdb            dimension tables + index
    ├── raw/
    │   └── encounter_id=<E>/difficulty=<D>/partition=<P>/<file>.parquet
    └── profiles/
        └── <cohort_id>.parquet

Nested PlayerLog fields (cast_timeline, damage_by_ability, uptimes,
resource_waste) are never queried at the SQL level, so they're stored as
JSON-string columns in the per-log parquet file rather than as native
Arrow nested/map types — simpler and robust, at the cost of not being
directly queryable by DuckDB. The `logs` table holds only the flat,
indexable summary fields plus a pointer to the parquet file. The actual
Parquet (de)serialization lives in ingest/parquet_codec.py (T1.6 split,
to keep this file under the 300-line limit).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from botgitgud.domain.models import CohortProfile, PlayerLog, RunManifest
from botgitgud.ingest.parquet_codec import (
    read_parquet_log,
    read_parquet_profile,
    write_parquet_log,
    write_parquet_profile,
)

_CREATE_LOGS_TABLE = """
CREATE TABLE IF NOT EXISTS logs (
    report_code   VARCHAR, fight_id INTEGER, player_name VARCHAR, server VARCHAR,
    encounter_id  INTEGER, difficulty INTEGER, partition INTEGER,
    class_name    VARCHAR, spec_name VARCHAR, role VARCHAR,
    duration_s    DOUBLE,  dps DOUBLE, percentile DOUBLE,
    item_level    DOUBLE,  talent_hash VARCHAR, tier_pieces INTEGER,
    active_time_pct DOUBLE, deaths INTEGER,
    parquet_path  VARCHAR, ingested_at TIMESTAMP
)
"""
# docs/desvios.md D-12(c): deliberately no PRIMARY KEY — a second insert for
# the same (report_code, fight_id, player_name) must SUCCEED (immutability:
# re-ingestion adds a row with a later ingested_at; reads take the latest).

_CREATE_COHORTS_TABLE = """
CREATE TABLE IF NOT EXISTS cohorts (
    cohort_id VARCHAR PRIMARY KEY, criteria_json VARCHAR,
    n_members INTEGER, built_at TIMESTAMP, code_version VARCHAR
)
"""
# A cohort_id is a content hash of its criteria (T1.5) — rebuilding the same
# cohort should replace its row, not duplicate it, so this table IS upserted.

_CREATE_SPELLS_TABLE = """
CREATE TABLE IF NOT EXISTS spells (
    spell_id BIGINT PRIMARY KEY, name VARCHAR, source VARCHAR,
    base_cooldown_s DOUBLE, updated_at TIMESTAMP
)
"""
# Table created per the T1.3 schema; no task yet asks Store to expose
# read/write methods for it (SpellCatalog, T0.4, still owns spell data via
# its own JSON file) — left empty and unused until a later task wires it up.

_CREATE_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS runs (
    cohort_id VARCHAR, code_version VARCHAR, generated_at TIMESTAMP,
    n_members INTEGER, wcl_partition INTEGER, settings_hash VARCHAR
)
"""
# T1.5: insert-only audit log of every report generated — no PK, same
# immutability rationale as `logs` (D-12c): a repeated run is a new fact,
# not a duplicate to reject.


class Store:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._raw_dir = data_dir / "raw"
        self._profiles_dir = data_dir / "profiles"
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        self._profiles_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = data_dir / "warehouse.duckdb"
        self._conn = duckdb.connect(str(self._db_path))
        self._conn.execute(_CREATE_LOGS_TABLE)
        self._conn.execute(_CREATE_COHORTS_TABLE)
        self._conn.execute(_CREATE_SPELLS_TABLE)
        self._conn.execute(_CREATE_RUNS_TABLE)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- logs -------------------------------------------------------------------

    def has_log(self, report_code: str, fight_id: int, player: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM logs WHERE report_code = ? AND fight_id = ? AND player_name = ? LIMIT 1",
            [report_code, fight_id, player],
        ).fetchone()
        return row is not None

    def read_log(self, report_code: str, fight_id: int, player: str) -> PlayerLog | None:
        row = self._conn.execute(
            """
            SELECT parquet_path FROM logs
            WHERE report_code = ? AND fight_id = ? AND player_name = ?
            ORDER BY ingested_at DESC
            LIMIT 1
            """,
            [report_code, fight_id, player],
        ).fetchone()
        if row is None:
            return None
        return read_parquet_log(Path(row[0]))

    def write_log(self, log: PlayerLog) -> None:
        fight = log.fight
        build = log.build

        partition_component = "unknown" if fight.partition is None else str(fight.partition)
        raw_dir = (
            self._raw_dir
            / f"encounter_id={fight.encounter_id}"
            / f"difficulty={fight.difficulty}"
            / f"partition={partition_component}"
        )
        raw_dir.mkdir(parents=True, exist_ok=True)

        # docs/desvios.md D-12(d): player + ingestion timestamp in the
        # filename — a bare <report_code>_<fight_id> would collide across
        # the many players in one fight, and across re-ingestions of the
        # same player.
        ingested_at_ms = int(time.time() * 1000)
        safe_player = build.character_name.replace("/", "_").replace("\\", "_")
        filename = f"{fight.report_code}_{fight.fight_id}_{safe_player}_{ingested_at_ms}.parquet"
        parquet_path = raw_dir / filename
        write_parquet_log(log, parquet_path)

        self._conn.execute(
            """
            INSERT INTO logs (
                report_code, fight_id, player_name, server, encounter_id, difficulty, partition,
                class_name, spec_name, role, duration_s, dps, percentile,
                item_level, talent_hash, tier_pieces, active_time_pct, deaths,
                parquet_path, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                fight.report_code,
                fight.fight_id,
                build.character_name,
                build.server,
                fight.encounter_id,
                fight.difficulty,
                fight.partition,
                build.class_name,
                build.spec_name,
                build.role,
                fight.duration_s,
                log.dps,
                log.percentile,
                build.item_level,
                build.talent_hash,
                build.tier_pieces,
                log.active_time_pct,
                log.deaths,
                str(parquet_path),
                datetime.fromtimestamp(ingested_at_ms / 1000.0, tz=UTC),
            ],
        )

    # -- cohort profiles ----------------------------------------------------------

    def read_profile(self, cohort_id: str) -> CohortProfile | None:
        path = self._profiles_dir / f"{cohort_id}.parquet"
        if not path.exists():
            return None
        return read_parquet_profile(path)

    def write_profile(self, profile: CohortProfile) -> None:
        path = self._profiles_dir / f"{profile.cohort_id}.parquet"
        write_parquet_profile(profile, path)
        self._conn.execute(
            """
            INSERT INTO cohorts (cohort_id, criteria_json, n_members, built_at, code_version)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (cohort_id) DO UPDATE SET
                n_members = excluded.n_members,
                built_at = excluded.built_at
            """,
            [profile.cohort_id, "{}", profile.n_members, profile.built_at, "unknown"],
        )

    # -- run manifests --------------------------------------------------------------

    def write_run(self, manifest: RunManifest) -> None:
        self._conn.execute(
            """
            INSERT INTO runs (
                cohort_id, code_version, generated_at, n_members, wcl_partition, settings_hash
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                manifest.cohort_id,
                manifest.code_version,
                manifest.generated_at,
                manifest.n_members,
                manifest.wcl_partition,
                manifest.settings_hash,
            ],
        )

    # -- generic SQL --------------------------------------------------------------

    def query(self, sql: str, **params: Any) -> pl.DataFrame:
        return self._conn.execute(sql, params).pl()
