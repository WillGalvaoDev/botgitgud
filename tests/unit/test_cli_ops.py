from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from botgitgud.bot.job_models import BudgetStatus
from botgitgud.bot.jobs import JobQueue
from botgitgud.cli import build_parser
from botgitgud.cli_ops import _cmd_ops_status, _cmd_recover_jobs, warehouse_status
from botgitgud.ingest.store import Store


def test_status_missing_warehouse_is_read_only(tmp_path: Path) -> None:
    lines = warehouse_status(tmp_path)
    assert lines == [f"warehouse=missing path={tmp_path / 'warehouse.duckdb'}"]
    assert list(tmp_path.iterdir()) == []


def test_status_reports_queue_by_final_state(tmp_path: Path) -> None:
    db = tmp_path / "warehouse.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute("CREATE TABLE jobs(status VARCHAR)")
    conn.execute("INSERT INTO jobs VALUES ('queued'), ('running'), ('done')")
    conn.close()
    lines = warehouse_status(tmp_path)
    assert "warehouse=ok" in lines
    assert "jobs queued=1 running=1 failed=0 done=1" in lines


def test_recover_jobs_transitions_running_to_queued(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with Store(tmp_path) as store:
        queue = JobQueue(store)
        queue.enqueue(
            job_type="analyze",
            dedup_key="ABCDEFGHIJKLMNOP:1:Zarad",
            discord_user_id="u",
            discord_channel_id="c",
        )
        assert queue.claim_next(BudgetStatus(3600.0, 3600.0)) is not None
    args = build_parser().parse_args(["recover-jobs", "--data-dir", str(tmp_path)])
    assert _cmd_recover_jobs(args) == 0
    assert capsys.readouterr().out == "recovered_jobs=1\n"
    with duckdb.connect(str(tmp_path / "warehouse.duckdb"), read_only=True) as conn:
        assert conn.execute("SELECT status, started_at FROM jobs").fetchone() == ("queued", None)


def test_ops_status_exit_code_and_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = build_parser().parse_args(["ops-status", "--data-dir", str(tmp_path)])
    assert _cmd_ops_status(args) == 0
    output = capsys.readouterr().out
    assert "warehouse=missing" in output
    assert "live_budget=not_queried" in output
