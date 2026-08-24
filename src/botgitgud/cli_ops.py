"""Local operational diagnostics and crash recovery for the v1.0 runbook."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

from botgitgud.bot.jobs import JobQueue
from botgitgud.ingest.store import Store


def warehouse_status(data_dir: Path) -> list[str]:
    """Inspect the warehouse read-only; never creates tables or calls an API."""
    db_path = data_dir / "warehouse.duckdb"
    if not db_path.is_file():
        return [f"warehouse=missing path={db_path}"]
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        tables = {str(row[0]) for row in conn.execute("SHOW TABLES").fetchall()}
        lines = [
            "warehouse=ok",
            f"warehouse_bytes={db_path.stat().st_size}",
            f"raw_parquet_files={sum(1 for _ in (data_dir / 'raw').rglob('*.parquet'))}",
        ]
        if "jobs" not in tables:
            lines.append("jobs_table=missing queued=0 running=0 failed=0 done=0")
        else:
            counts = dict(
                conn.execute("SELECT status, count(*) FROM jobs GROUP BY status").fetchall()
            )
            lines.append(
                "jobs "
                + " ".join(
                    f"{status}={int(counts.get(status, 0))}"
                    for status in ("queued", "running", "failed", "done")
                )
            )
        lines.append("tables=" + ",".join(sorted(tables)))
        return lines
    finally:
        conn.close()


def _cmd_ops_status(args: argparse.Namespace) -> int:
    for line in warehouse_status(args.data_dir):
        sys.stdout.write(line + "\n")
    sys.stdout.write(f"api_points_floor={args.api_points_floor:.0f} live_budget=not_queried\n")
    return 0


def _cmd_recover_jobs(args: argparse.Namespace) -> int:
    with Store(args.data_dir) as store:
        recovered = JobQueue(store).recover_from_crash()
    sys.stdout.write(f"recovered_jobs={recovered}\n")
    return 0


def add_ops_parsers(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],  # type: ignore[name-defined]
) -> None:
    status = sub.add_parser("ops-status", help="Saúde local read-only do warehouse e da fila.")
    status.add_argument("--data-dir", type=Path, default=Path("data"))
    status.add_argument("--api-points-floor", type=float, default=1000.0)
    status.set_defaults(func=_cmd_ops_status)

    recover = sub.add_parser(
        "recover-jobs", help="Move jobs presos em running de volta para queued."
    )
    recover.add_argument("--data-dir", type=Path, default=Path("data"))
    recover.set_defaults(func=_cmd_recover_jobs)
