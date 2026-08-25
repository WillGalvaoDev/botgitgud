"""T1.3 — DuckDB + Parquet storage layer.

See docs/desvios.md D-12 for four gaps in this task's own spec (FightRef
missing `partition`, `CohortProfile` never defined anywhere, a PRIMARY KEY
that contradicts the "raw data is immutable" principle stated as
obligatory in the same section, and a parquet path template that collides
across players and re-ingestions) and how each was resolved.

Layout:
    data/
    ├── warehouse.duckdb            dimension tables + index
    └── raw/
        └── encounter_id=<E>/difficulty=<D>/partition=<P>/<file>.parquet

docs/desvios.md D-25 (T2.1): the T1.7 `profiles/<cohort_id>.parquet` +
`cohorts` table (a pre-aggregated CohortProfile cached per bucket) is gone
— T2.1's per-player covariate matching (analysis/cohort_match.py) means
the aggregate can no longer be precomputed once and reused across every
player who lands in the same bucket; it must be built fresh from
`build_cd_reference_profile` on every request, from whichever reference
logs survive that player's own matching cascade. What IS still safe to
cache per cohort_id is the *candidate pool* (`cohort_candidates` below):
the same ranking page fetch produces it regardless of which player is
being analyzed, so a warm request still spends zero characterRankings
queries — pipeline.py's own per-request match_cohort() call does the rest
in-memory.

Nested PlayerLog fields (cast_timeline, damage_by_ability, uptimes,
resource_waste) are never queried at the SQL level, so they're stored as
JSON-string columns in the per-log parquet file rather than as native
Arrow nested/map types — simpler and robust, at the cost of not being
directly queryable by DuckDB. The `logs` table holds only the flat,
indexable summary fields plus a pointer to the parquet file. The actual
Parquet (de)serialization lives in ingest/parquet_codec.py (T1.6 split,
to keep this file under the 300-line limit).

T1.8: a single duckdb.Connection isn't safe to use concurrently from
multiple threads. Every access — reads included — goes through
self._lock, serializing them onto whichever thread happens to hold it at
the time. This satisfies T1.8's "toda escrita passa por um writer único
serializado... nunca abra conexões a partir dos workers" requirement: a
worker thread calling write_log()/enqueue_job() never touches the
connection directly outside the lock, and the lock (not a literal
dedicated thread + work queue) is what actually enforces one-at-a-time
access — see docs/desvios.md D-19 for why this achieves the same
contract with a simpler, deadlock-free mechanism.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from botgitgud.domain.models import CohortCriteria, PlayerLog, RankingCandidate, RunManifest
from botgitgud.ingest.parquet_codec import read_parquet_log, write_parquet_log

_CREATE_LOGS_TABLE = """
CREATE TABLE IF NOT EXISTS logs (
    report_code   VARCHAR, fight_id INTEGER, player_name VARCHAR, server VARCHAR,
    encounter_id  INTEGER, difficulty INTEGER, partition INTEGER,
    class_name    VARCHAR, spec_name VARCHAR, role VARCHAR,
    duration_s    DOUBLE,  dps DOUBLE, percentile DOUBLE,
    item_level    DOUBLE,  talent_hash VARCHAR, tier_pieces INTEGER,
    active_time_pct DOUBLE, deaths INTEGER, downtime_s DOUBLE, kill BOOLEAN,
    parquet_path  VARCHAR, ingested_at TIMESTAMP
)
"""
# T-DG.5: `kill` was already on FightRef/Parquet (T1.2) but never promoted
# to this flat, indexable table — the Data Acquisition Gate's validity
# contract (docs/fase4-data-acquisition-plan.md §10.2) needs to filter on
# it at SQL level without opening every log's Parquet file (same reasoning
# T-DG.0 used for `partition`).
# docs/desvios.md D-12(c): deliberately no PRIMARY KEY — a second insert for
# the same (report_code, fight_id, player_name) must SUCCEED (immutability:
# re-ingestion adds a row with a later ingested_at; reads take the latest).

_CREATE_CANDIDATES_TABLE = """
CREATE TABLE IF NOT EXISTS cohort_candidates (
    cohort_id VARCHAR, report_code VARCHAR, fight_id INTEGER,
    player_name VARCHAR, duration_s DOUBLE
)
"""
_CREATE_COHORT_REGISTRY_TABLE = """
CREATE TABLE IF NOT EXISTS cohort_registry (
    cohort_id VARCHAR PRIMARY KEY, encounter_id INTEGER, difficulty INTEGER,
    partition INTEGER, class_name VARCHAR, spec_name VARCHAR,
    duration_min_s DOUBLE, duration_max_s DOUBLE, n_members INTEGER,
    updated_at TIMESTAMP
)
"""
# D-25: replaces T1.7's `cohorts` table. A cohort_id's candidate pool is
# replaced wholesale on rebuild (DELETE + re-INSERT in write_candidate_pool)
# rather than upserted row-by-row — the pool is one atomic unit, not a set
# of independently-updatable facts like `logs`/`runs`.

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
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = data_dir / "warehouse.duckdb"
        self._lock = threading.RLock()
        self._conn = duckdb.connect(str(self._db_path))
        self._conn.execute(_CREATE_LOGS_TABLE)
        self._conn.execute(_CREATE_CANDIDATES_TABLE)
        self._conn.execute(_CREATE_COHORT_REGISTRY_TABLE)
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
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM logs WHERE report_code = ? AND fight_id = ? "
                "AND player_name = ? LIMIT 1",
                [report_code, fight_id, player],
            ).fetchone()
        return row is not None

    def read_log(self, report_code: str, fight_id: int, player: str) -> PlayerLog | None:
        with self._lock:
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

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO logs (
                    report_code, fight_id, player_name, server, encounter_id, difficulty,
                    partition, class_name, spec_name, role, duration_s, dps, percentile,
                    item_level, talent_hash, tier_pieces, active_time_pct, deaths, downtime_s,
                    kill, parquet_path, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    log.downtime_s,
                    fight.kill,
                    str(parquet_path),
                    datetime.fromtimestamp(ingested_at_ms / 1000.0, tz=UTC),
                ],
            )

    # -- cohort candidate pools (T2.1, D-25) ---------------------------------------

    def read_candidate_pool(self, cohort_id: str) -> list[RankingCandidate] | None:
        """None means "never built" — an empty list is a valid, previously-
        recorded outcome (a bucket whose only candidates all failed to
        parse) and callers must not treat the two the same way.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT report_code, fight_id, player_name, duration_s "
                "FROM cohort_candidates WHERE cohort_id = ?",
                [cohort_id],
            ).fetchall()
        if not rows:
            return None
        return [
            RankingCandidate(report_code=r[0], fight_id=r[1], player_name=r[2], duration_s=r[3])
            for r in rows
        ]

    def write_candidate_pool(
        self,
        cohort_id: str,
        candidates: list[RankingCandidate],
        *,
        criteria: CohortCriteria | None = None,
    ) -> None:
        rows = [
            (cohort_id, c.report_code, c.fight_id, c.player_name, c.duration_s) for c in candidates
        ]
        with self._lock:
            self._conn.execute("DELETE FROM cohort_candidates WHERE cohort_id = ?", [cohort_id])
            self._conn.executemany(
                "INSERT INTO cohort_candidates "
                "(cohort_id, report_code, fight_id, player_name, duration_s) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            if criteria is not None:
                self._conn.execute(
                    """INSERT OR REPLACE INTO cohort_registry
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        cohort_id,
                        criteria.encounter_id,
                        criteria.difficulty,
                        criteria.partition,
                        criteria.class_name,
                        criteria.spec_name,
                        criteria.duration_min_s,
                        criteria.duration_max_s,
                        len(candidates),
                        datetime.now(UTC).replace(tzinfo=None),
                    ],
                )

    def list_ready_cohorts(self) -> list[dict[str, object]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT cohort_id, encounter_id, difficulty, partition, class_name,
                          spec_name, duration_min_s, duration_max_s, n_members, updated_at
                   FROM cohort_registry ORDER BY updated_at DESC"""
            ).fetchall()
        keys = (
            "cohort_id",
            "encounter_id",
            "difficulty",
            "partition",
            "class_name",
            "spec_name",
            "duration_min_s",
            "duration_max_s",
            "n_members",
            "updated_at",
        )
        return [dict(zip(keys, row, strict=True)) for row in rows]

    # -- run manifests --------------------------------------------------------------

    def write_run(self, manifest: RunManifest) -> None:
        with self._lock:
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

    # -- generic SQL (T1.8: also used by bot/jobs.py's JobQueue for the `jobs`
    # table, so every DB access in the process shares this one lock) -------------

    def query(self, sql: str, **params: Any) -> pl.DataFrame:
        with self._lock:
            return self._conn.execute(sql, params).pl()

    def execute(self, sql: str, params: list[Any] | None = None) -> None:
        with self._lock:
            self._conn.execute(sql, params or [])

    def execute_returning(self, sql: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
        with self._lock:
            return self._conn.execute(sql, params or []).fetchall()
