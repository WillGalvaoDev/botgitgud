from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb
import pytest

from botgitgud.bot.job_models import BudgetStatus, Job, now_utc_naive
from botgitgud.bot.jobs import JobQueue
from botgitgud.bot.ops_snapshot import read_snapshot, write_snapshot
from botgitgud.cli import EX_TEMPFAIL, build_parser
from botgitgud.cli_ops import _cmd_ops_status, _cmd_recover_jobs, warehouse_status
from botgitgud.ingest.store import Store


def _running_job() -> Job:
    return Job(
        job_id="a",
        job_type="analyze",  # type: ignore[arg-type]
        dedup_key="ABCDEFGHIJKLMNOP:1:Zarad",
        discord_user_id="u",
        discord_channel_id="c",
        status="running",  # type: ignore[arg-type]
        created_at=now_utc_naive(),
        started_at=now_utc_naive(),
        finished_at=None,
        error=None,
        report_path=None,
    )


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
    assert lines is not None
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


def test_ops_snapshot_schema_atomicity_freshness_and_cohort_observability(tmp_path: Path) -> None:
    ready = [{"cohort_id": "abc", "class_name": "Warlock", "spec_name": "Demonology"}]
    write_snapshot(
        tmp_path,
        pid=42,
        active=[_running_job()],
        ready_cohorts=ready,
        cold_build={"stage": "build_started", "cohort_id": "abc"},
        now=100.0,
    )
    snapshot = read_snapshot(tmp_path, now=105.0)
    assert snapshot is not None
    # EB.5: 3 (não 2) — bump legítimo de schema ao adicionar `by_type`
    # (contagem de jobs por job_type x status), não uma regressão.
    assert snapshot.schema_version == 3
    assert snapshot.worker_alive
    assert snapshot.age_seconds == 5.0 and not snapshot.is_stale
    assert snapshot.ready_cohorts == ready
    assert snapshot.cold_build == {"stage": "build_started", "cohort_id": "abc"}
    assert not (tmp_path / "ops-snapshot.json.tmp").exists()


# -- R3-01 reaberta: warehouse travado por um `serve` em execução (D-34) ---------
#
# O smoke A de R1-01 provou que o DuckDB 1.5.5 nega até conexão read_only
# enquanto outro processo segura o arquivo, e que ops-status despejava
# traceback cru. Estes testes reproduzem o lock de verdade, entre processos.


_HOLDER = """
import duckdb, sys, time
conn = duckdb.connect(sys.argv[1])
conn.execute("CREATE TABLE IF NOT EXISTS jobs(status VARCHAR)")
sys.stdout.write("ready\\n")
sys.stdout.flush()
time.sleep(float(sys.argv[2]))
"""


@contextmanager
def _warehouse_locked_by_another_process(data_dir: Path) -> Iterator[int]:
    """Segura o warehouse em outro processo, exatamente como o `serve` faz."""
    data_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(data_dir / "warehouse.duckdb"), "30"],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "ready"
        yield proc.pid
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_locked_warehouse_without_snapshot_is_a_controlled_error_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with _warehouse_locked_by_another_process(tmp_path):
        args = build_parser().parse_args(["ops-status", "--data-dir", str(tmp_path)])
        exit_code = _cmd_ops_status(args)
    captured = capsys.readouterr()
    assert exit_code == EX_TEMPFAIL
    assert "Traceback" not in captured.err
    assert "IOException" not in captured.err
    assert "em uso por outro processo" in captured.err
    assert "ops-snapshot" in captured.err


def test_locked_warehouse_with_snapshot_reports_live_state_and_succeeds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_snapshot(tmp_path, pid=4242, active=[], points_remaining=2600.0, points_limit=3600.0)
    with _warehouse_locked_by_another_process(tmp_path):
        args = build_parser().parse_args(["ops-status", "--data-dir", str(tmp_path)])
        exit_code = _cmd_ops_status(args)
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "source=running_bot" in output
    assert "bot_pid=4242" in output
    assert "jobs queued=0 running=0" in output
    assert "points_remaining=2600" in output


def test_locked_warehouse_snapshot_reports_a_stuck_job(tmp_path: Path) -> None:
    write_snapshot(tmp_path, pid=1, active=[_running_job()])
    with _warehouse_locked_by_another_process(tmp_path):
        lines = warehouse_status(tmp_path)
    assert lines is not None
    text = "\n".join(lines)
    assert "source=running_bot" in text
    assert "running=1" in text
    assert "oldest_running_started_at=" in text


def test_ops_status_never_queries_wcl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nem o caminho local nem o do snapshot podem tocar a rede."""
    import httpx

    def explode(*_args: object, **_kwargs: object) -> None:
        pytest.fail("ops-status must never make an HTTP request")

    monkeypatch.setattr(httpx.Client, "send", explode)
    monkeypatch.setattr(httpx.Client, "request", explode)
    write_snapshot(tmp_path, pid=1, active=[])
    with _warehouse_locked_by_another_process(tmp_path):
        args = build_parser().parse_args(["ops-status", "--data-dir", str(tmp_path)])
        assert _cmd_ops_status(args) == 0


def test_recover_jobs_on_a_locked_warehouse_is_a_controlled_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Recuperar job exige escrita, logo exige o bot parado — mas a recusa
    precisa ser uma mensagem acionável, não um traceback.
    """
    with _warehouse_locked_by_another_process(tmp_path):
        args = build_parser().parse_args(["recover-jobs", "--data-dir", str(tmp_path)])
        exit_code = _cmd_recover_jobs(args)
    captured = capsys.readouterr()
    assert exit_code == EX_TEMPFAIL
    assert "Traceback" not in captured.err
    assert "pare o bot" in captured.err.lower()
