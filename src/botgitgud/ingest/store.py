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
indexable summary fields plus a pointer to the parquet file.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from botgitgud.domain.models import (
    AbilityDamage,
    CohortProfile,
    FightRef,
    PlayerBuild,
    PlayerLog,
    SpellProfile,
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
        return _read_parquet_log(Path(row[0]))

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
        _write_parquet_log(log, parquet_path)

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
        return _read_parquet_profile(path)

    def write_profile(self, profile: CohortProfile) -> None:
        path = self._profiles_dir / f"{profile.cohort_id}.parquet"
        _write_parquet_profile(profile, path)
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

    # -- generic SQL --------------------------------------------------------------

    def query(self, sql: str, **params: Any) -> pl.DataFrame:
        return self._conn.execute(sql, params).pl()


# -- parquet (de)serialization ---------------------------------------------------


def _write_parquet_log(log: PlayerLog, path: Path) -> None:
    fight = log.fight
    build = log.build

    cast_timeline_json = json.dumps({str(k): list(v) for k, v in log.cast_timeline.items()})
    damage_by_ability_json = json.dumps(
        {
            str(k): {"spell_id": v.spell_id, "total": v.total, "hits": v.hits, "casts": v.casts}
            for k, v in log.damage_by_ability.items()
        }
    )
    uptimes_json = json.dumps({str(k): v for k, v in log.uptimes.items()})
    resource_waste_json = json.dumps(dict(log.resource_waste))

    table = pa.table(
        {
            "report_code": [fight.report_code],
            "fight_id": [fight.fight_id],
            "encounter_id": [fight.encounter_id],
            "boss_name": [fight.boss_name],
            "difficulty": [fight.difficulty],
            "duration_s": [fight.duration_s],
            "kill": [fight.kill],
            "partition": [fight.partition],
            "character_name": [build.character_name],
            "server": [build.server],
            "class_name": [build.class_name],
            "spec_name": [build.spec_name],
            "role": [build.role],
            "item_level": [build.item_level],
            "talent_hash": [build.talent_hash],
            "tier_pieces": [build.tier_pieces],
            "external_buffs": [list(build.external_buffs)],
            "dps": [log.dps],
            "percentile": [log.percentile],
            "active_time_pct": [log.active_time_pct],
            "deaths": [log.deaths],
            "cast_timeline_json": [cast_timeline_json],
            "damage_by_ability_json": [damage_by_ability_json],
            "uptimes_json": [uptimes_json],
            "resource_waste_json": [resource_waste_json],
        }
    )
    pq.write_table(table, path)


def _read_parquet_log(path: Path) -> PlayerLog:
    row = pq.read_table(path).to_pylist()[0]

    fight = FightRef(
        report_code=row["report_code"],
        fight_id=row["fight_id"],
        encounter_id=row["encounter_id"],
        boss_name=row["boss_name"],
        difficulty=row["difficulty"],
        duration_s=row["duration_s"],
        kill=row["kill"],
        partition=row["partition"],
    )
    build = PlayerBuild(
        character_name=row["character_name"],
        server=row["server"],
        class_name=row["class_name"],
        spec_name=row["spec_name"],
        role=row["role"],
        item_level=row["item_level"],
        talent_hash=row["talent_hash"],
        tier_pieces=row["tier_pieces"],
        external_buffs=frozenset(row["external_buffs"] or []),
    )
    cast_timeline = {int(k): tuple(v) for k, v in json.loads(row["cast_timeline_json"]).items()}
    damage_by_ability = {
        int(k): AbilityDamage(**v) for k, v in json.loads(row["damage_by_ability_json"]).items()
    }
    uptimes = {int(k): v for k, v in json.loads(row["uptimes_json"]).items()}
    resource_waste = json.loads(row["resource_waste_json"])

    return PlayerLog(
        fight=fight,
        build=build,
        dps=row["dps"],
        percentile=row["percentile"],
        cast_timeline=cast_timeline,
        active_time_pct=row["active_time_pct"],
        damage_by_ability=damage_by_ability,
        uptimes=uptimes,
        resource_waste=resource_waste,
        deaths=row["deaths"],
    )


def _write_parquet_profile(profile: CohortProfile, path: Path) -> None:
    spells_json = json.dumps(
        {
            str(sid): {
                "spell_id": sp.spell_id,
                "presence": sp.presence,
                "ref_times": list(sp.ref_times),
                "n_usages_median": sp.n_usages_median,
            }
            for sid, sp in profile.spells.items()
        }
    )
    table = pa.table(
        {
            "cohort_id": [profile.cohort_id],
            "n_members": [profile.n_members],
            "built_at": [profile.built_at.isoformat()],
            "spells_json": [spells_json],
        }
    )
    pq.write_table(table, path)


def _read_parquet_profile(path: Path) -> CohortProfile:
    row = pq.read_table(path).to_pylist()[0]
    spells_raw = json.loads(row["spells_json"])
    spells = {
        int(sid): SpellProfile(
            spell_id=v["spell_id"],
            presence=v["presence"],
            ref_times=tuple(v["ref_times"]),
            n_usages_median=v["n_usages_median"],
        )
        for sid, v in spells_raw.items()
    }
    return CohortProfile(
        cohort_id=row["cohort_id"],
        n_members=row["n_members"],
        built_at=datetime.fromisoformat(row["built_at"]),
        spells=spells,
    )
