"""R3-01 (reaberta) — o snapshot operacional que o `serve` publica enquanto
segura o lock do DuckDB, e que `ops-status` lê quando não consegue abrir o
warehouse. Ver docs/architecture.md D-34.
"""

from __future__ import annotations

import json
from pathlib import Path

from botgitgud.bot.job_models import Job, now_utc_naive
from botgitgud.bot.ops_snapshot import (
    SNAPSHOT_FILENAME,
    OpsSnapshot,
    read_snapshot,
    write_snapshot,
)


def _job(job_id: str, status: str) -> Job:
    return Job(
        job_id=job_id,
        job_type="analyze",  # type: ignore[arg-type]
        dedup_key="ABCDEFGHIJKLMNOP:1:Zarad",
        discord_user_id="u",
        discord_channel_id="c",
        status=status,  # type: ignore[arg-type]
        created_at=now_utc_naive(),
        started_at=now_utc_naive() if status == "running" else None,
        finished_at=None,
        error=None,
        report_path=None,
    )


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    write_snapshot(tmp_path, pid=4242, active=[_job("a", "queued"), _job("b", "running")])
    snapshot = read_snapshot(tmp_path)
    assert snapshot is not None
    assert snapshot.pid == 4242
    assert snapshot.queued == 1
    assert snapshot.running == 1


def test_read_returns_none_when_absent(tmp_path: Path) -> None:
    assert read_snapshot(tmp_path) is None


def test_read_returns_none_on_corrupt_file_instead_of_raising(tmp_path: Path) -> None:
    (tmp_path / SNAPSHOT_FILENAME).write_text("{not json", encoding="utf-8")
    assert read_snapshot(tmp_path) is None


def test_empty_queue_is_reported_as_zeros_not_as_missing_data(tmp_path: Path) -> None:
    write_snapshot(tmp_path, pid=1, active=[])
    snapshot = read_snapshot(tmp_path)
    assert snapshot is not None
    assert (snapshot.queued, snapshot.running) == (0, 0)
    assert snapshot.oldest_running_started_at is None


def test_running_job_start_time_is_published_for_stuck_job_diagnosis(tmp_path: Path) -> None:
    write_snapshot(tmp_path, pid=1, active=[_job("a", "running")])
    snapshot = read_snapshot(tmp_path)
    assert snapshot is not None
    assert snapshot.oldest_running_started_at is not None


def test_budget_is_never_invented_when_the_bot_has_not_checked_it(tmp_path: Path) -> None:
    """The bot only calls refresh_budget() when a job is queued. An idle bot
    has no budget reading, and the snapshot must say so rather than print a
    zero that reads like an exhausted budget.
    """
    write_snapshot(tmp_path, pid=1, active=[])
    snapshot = read_snapshot(tmp_path)
    assert snapshot is not None
    assert snapshot.points_remaining is None
    assert snapshot.budget_checked_at is None


def test_budget_is_carried_through_when_the_bot_did_check_it(tmp_path: Path) -> None:
    write_snapshot(tmp_path, pid=1, active=[], points_remaining=2600.0, points_limit=3600.0)
    snapshot = read_snapshot(tmp_path)
    assert snapshot is not None
    assert snapshot.points_remaining == 2600.0
    assert snapshot.points_limit == 3600.0
    assert snapshot.budget_checked_at is not None


def test_write_is_atomic_and_leaves_no_partial_file(tmp_path: Path) -> None:
    for _ in range(3):
        write_snapshot(tmp_path, pid=1, active=[])
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [SNAPSHOT_FILENAME]


def test_snapshot_never_contains_credentials(tmp_path: Path) -> None:
    write_snapshot(tmp_path, pid=1, active=[_job("a", "running")])
    payload = json.loads((tmp_path / SNAPSHOT_FILENAME).read_text(encoding="utf-8"))
    assert set(payload) <= set(OpsSnapshot.__dataclass_fields__)
    flat = json.dumps(payload).lower()
    for forbidden in ("token", "secret", "client_id", "authorization", "bearer"):
        assert forbidden not in flat


def test_age_seconds_grows_with_a_stale_snapshot(tmp_path: Path) -> None:
    write_snapshot(tmp_path, pid=1, active=[], now=1_000.0)
    snapshot = read_snapshot(tmp_path, now=1_090.0)
    assert snapshot is not None
    assert snapshot.age_seconds == 90.0
    assert snapshot.is_stale is True


def test_fresh_snapshot_is_not_stale(tmp_path: Path) -> None:
    write_snapshot(tmp_path, pid=1, active=[], now=1_000.0)
    snapshot = read_snapshot(tmp_path, now=1_003.0)
    assert snapshot is not None
    assert snapshot.is_stale is False
