"""T-DG.2 (docs/phase4.md) — persistence for
the Data Acquisition Gate's three-stage discovery pipeline: Estágio A
(reportData.reports) writes `discovery_reports`; Estágio B
(report.rankings, ingest/fight_rankings.py) writes `discovery_fights` +
`discovery_targets`. `backfill_checkpoints` is what makes Estágio A
resumable and safe to interrupt (§9.2) — one row per (job_key, window),
so killing the process mid-scan loses at most the page in flight.

Follows bot/jobs.py's JobQueue pattern: owns its own DDL and talks to the
shared warehouse only through Store's public execute/execute_returning/
query methods (T1.8 — every DB access in the process serializes through
Store's one connection+lock; see ingest/store.py's module docstring),
never touching Store._conn directly.

Every table here is idempotent-upsert (`INSERT OR REPLACE`) by its natural
key — re-scanning a window or re-triaging a fight overwrites, never
duplicates (§9.1/§9.3: dedup by construction, not a post-hoc filter). This
is a deliberate departure from `logs`'/`runs`' insert-only immutability
(D-12c): discovery/checkpoint rows are the *current state of a scan*, an
index to be refreshed, not an immutable observation like a PlayerLog —
`logs` itself is untouched by this module and keeps its no-PK contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from botgitgud.ingest.fight_rankings import FightRankings
from botgitgud.ingest.store import Store

CheckpointState = Literal["pending", "in_progress", "done", "exhausted_cap"]

_CREATE_DISCOVERY_REPORTS_TABLE = """
CREATE TABLE IF NOT EXISTS discovery_reports (
    report_code VARCHAR PRIMARY KEY,
    zone_id INTEGER, start_time_ms BIGINT, end_time_ms BIGINT,
    discovered_at TIMESTAMP,
    triaged_at TIMESTAMP
)
"""
# T-DG.4: `triaged_at` (NULL = not yet triaged) tracks Estágio B's own
# dedup unit — an entire report, not a page or a fight (report.rankings
# with no fightIDs filter triages every ranked fight of a report in one
# call, ingest/fight_rankings.py's parse_report_rankings_all). `write_report`
# below never lists this column, so DuckDB's INSERT OR REPLACE leaves an
# already-triaged report's triaged_at untouched on a re-discovery
# (verified: DuckDB's INSERT OR REPLACE only overwrites listed columns,
# unlike SQLite's delete+reinsert semantics).

# Estágio B (docs/schema_confirmado.md §13.3): one row per triaged fight —
# partition/kill/duration come from report.rankings, the strong source
# T-DG.0/T-DG.1 already established for FightRef.partition.
_CREATE_DISCOVERY_FIGHTS_TABLE = """
CREATE TABLE IF NOT EXISTS discovery_fights (
    report_code VARCHAR, fight_id INTEGER,
    partition INTEGER, encounter_id INTEGER, difficulty INTEGER,
    size INTEGER, kill BOOLEAN, duration_s DOUBLE,
    triaged_at TIMESTAMP,
    PRIMARY KEY (report_code, fight_id)
)
"""

# One row per DPS character in a triaged fight (dps-only: T0.9, the tool's
# entire scope) — the census input for choosing the pilot target (§7.3).
_CREATE_DISCOVERY_TARGETS_TABLE = """
CREATE TABLE IF NOT EXISTS discovery_targets (
    report_code VARCHAR, fight_id INTEGER, player_name VARCHAR,
    server_name VARCHAR, server_region VARCHAR,
    class_name VARCHAR, spec_name VARCHAR,
    amount DOUBLE, rank_percent DOUBLE, bracket_data DOUBLE, total_parses INTEGER,
    PRIMARY KEY (report_code, fight_id, player_name)
)
"""

_CREATE_BACKFILL_CHECKPOINTS_TABLE = """
CREATE TABLE IF NOT EXISTS backfill_checkpoints (
    job_key VARCHAR, zone_id INTEGER,
    window_start BIGINT, window_end BIGINT,
    last_page INTEGER, state VARCHAR, points_spent DOUBLE,
    updated_at TIMESTAMP,
    PRIMARY KEY (job_key, window_start, window_end)
)
"""


@dataclass(frozen=True, slots=True)
class Checkpoint:
    job_key: str
    zone_id: int
    window_start: int
    window_end: int
    last_page: int
    state: CheckpointState
    points_spent: float
    updated_at: datetime


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class DiscoveryStore:
    def __init__(self, store: Store) -> None:
        self._store = store
        self._store.execute(_CREATE_DISCOVERY_REPORTS_TABLE)
        self._store.execute(_CREATE_DISCOVERY_FIGHTS_TABLE)
        self._store.execute(_CREATE_DISCOVERY_TARGETS_TABLE)
        self._store.execute(_CREATE_BACKFILL_CHECKPOINTS_TABLE)

    # -- Estágio A: discovery_reports --------------------------------------------

    def has_report(self, report_code: str) -> bool:
        rows = self._store.execute_returning(
            "SELECT 1 FROM discovery_reports WHERE report_code = ? LIMIT 1", [report_code]
        )
        return bool(rows)

    def write_report(
        self, *, report_code: str, zone_id: int, start_time_ms: int, end_time_ms: int
    ) -> None:
        self._store.execute(
            "INSERT OR REPLACE INTO discovery_reports "
            "(report_code, zone_id, start_time_ms, end_time_ms, discovered_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [report_code, zone_id, start_time_ms, end_time_ms, _now()],
        )

    def count_reports(self, *, zone_id: int | None = None) -> int:
        if zone_id is None:
            rows = self._store.execute_returning("SELECT count(*) FROM discovery_reports")
        else:
            rows = self._store.execute_returning(
                "SELECT count(*) FROM discovery_reports WHERE zone_id = ?", [zone_id]
            )
        return int(rows[0][0])

    # -- Estágio B dedup unit: an entire report (T-DG.4) -------------------------

    def has_triaged_report(self, report_code: str) -> bool:
        rows = self._store.execute_returning(
            "SELECT 1 FROM discovery_reports WHERE report_code = ? AND triaged_at IS NOT NULL "
            "LIMIT 1",
            [report_code],
        )
        return bool(rows)

    def mark_report_triaged(self, report_code: str) -> None:
        self._store.execute(
            "UPDATE discovery_reports SET triaged_at = ? WHERE report_code = ?",
            [_now(), report_code],
        )

    def list_untriaged_reports(self, *, zone_id: int | None = None) -> list[str]:
        if zone_id is None:
            rows = self._store.execute_returning(
                "SELECT report_code FROM discovery_reports WHERE triaged_at IS NULL "
                "ORDER BY report_code"
            )
        else:
            rows = self._store.execute_returning(
                "SELECT report_code FROM discovery_reports "
                "WHERE triaged_at IS NULL AND zone_id = ? ORDER BY report_code",
                [zone_id],
            )
        return [r[0] for r in rows]

    # -- Estágio B: discovery_fights + discovery_targets -------------------------

    def has_fight(self, report_code: str, fight_id: int) -> bool:
        rows = self._store.execute_returning(
            "SELECT 1 FROM discovery_fights WHERE report_code = ? AND fight_id = ? LIMIT 1",
            [report_code, fight_id],
        )
        return bool(rows)

    def write_fight_rankings(self, fight_rankings: FightRankings, *, report_code: str) -> None:
        """Persists both the fight-level triage row and every DPS
        character's row in one call — the two tables are always written
        together (they come from the same report.rankings response), so
        there is no separate write_targets method.
        """
        now = _now()
        self._store.execute(
            "INSERT OR REPLACE INTO discovery_fights "
            "(report_code, fight_id, partition, encounter_id, difficulty, size, kill, "
            "duration_s, triaged_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                report_code,
                fight_rankings.fight_id,
                fight_rankings.partition,
                fight_rankings.encounter_id,
                fight_rankings.difficulty,
                fight_rankings.size,
                fight_rankings.kill,
                fight_rankings.duration_s,
                now,
            ],
        )
        for dps in fight_rankings.dps:
            self._store.execute(
                "INSERT OR REPLACE INTO discovery_targets "
                "(report_code, fight_id, player_name, server_name, server_region, "
                "class_name, spec_name, amount, rank_percent, bracket_data, total_parses) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    report_code,
                    fight_rankings.fight_id,
                    dps.player_name,
                    dps.server_name,
                    dps.server_region,
                    dps.class_name,
                    dps.spec_name,
                    dps.amount,
                    dps.rank_percent,
                    dps.bracket_data,
                    dps.total_parses,
                ],
            )

    # -- checkpoints ----------------------------------------------------------------

    def read_checkpoint(
        self, job_key: str, window_start: int, window_end: int
    ) -> Checkpoint | None:
        rows = self._store.execute_returning(
            "SELECT job_key, zone_id, window_start, window_end, last_page, state, "
            "points_spent, updated_at FROM backfill_checkpoints "
            "WHERE job_key = ? AND window_start = ? AND window_end = ?",
            [job_key, window_start, window_end],
        )
        return _row_to_checkpoint(rows[0]) if rows else None

    def write_checkpoint(
        self,
        *,
        job_key: str,
        zone_id: int,
        window_start: int,
        window_end: int,
        last_page: int,
        state: CheckpointState,
        points_spent: float,
    ) -> None:
        self._store.execute(
            "INSERT OR REPLACE INTO backfill_checkpoints "
            "(job_key, zone_id, window_start, window_end, last_page, state, "
            "points_spent, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [job_key, zone_id, window_start, window_end, last_page, state, points_spent, _now()],
        )

    def list_checkpoints(self, job_key: str) -> list[Checkpoint]:
        """Ordered by window_start — the order Estágio A resumes in (§9.2:
        windows are closed, disjoint, and in the past, so this ordering is
        stable across runs).
        """
        rows = self._store.execute_returning(
            "SELECT job_key, zone_id, window_start, window_end, last_page, state, "
            "points_spent, updated_at FROM backfill_checkpoints "
            "WHERE job_key = ? ORDER BY window_start ASC",
            [job_key],
        )
        return [_row_to_checkpoint(r) for r in rows]


def _row_to_checkpoint(row: tuple[object, ...]) -> Checkpoint:
    return Checkpoint(
        job_key=row[0],  # type: ignore[arg-type]
        zone_id=row[1],  # type: ignore[arg-type]
        window_start=row[2],  # type: ignore[arg-type]
        window_end=row[3],  # type: ignore[arg-type]
        last_page=row[4],  # type: ignore[arg-type]
        state=row[5],  # type: ignore[arg-type]
        points_spent=row[6],  # type: ignore[arg-type]
        updated_at=row[7],  # type: ignore[arg-type]
    )
