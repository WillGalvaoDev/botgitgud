from __future__ import annotations

import threading
from pathlib import Path

import pytest

import botgitgud.bot.jobs as jobs_module
from botgitgud.bot.job_models import BudgetStatus
from botgitgud.bot.jobs import (
    MAX_ACTIVE_JOBS_PER_USER,
    MAX_CONCURRENT_JOBS,
    MAX_QUEUED_JOBS_PER_USER,
    JobQueue,
)
from botgitgud.ingest.store import Store


def _queue(tmp_path: Path) -> tuple[JobQueue, Store]:
    store = Store(tmp_path)
    return JobQueue(store), store


def _full_budget() -> BudgetStatus:
    return BudgetStatus(points_remaining=3600.0, limit_per_hour=3600.0)


# -- documented acceptance criteria ------------------------------------------------


def test_two_simultaneous_identical_requests_create_only_one_job(tmp_path: Path) -> None:
    queue, _store = _queue(tmp_path)
    results: list[object] = []
    barrier = threading.Barrier(2)

    def _request() -> None:
        barrier.wait(timeout=5)
        results.append(
            queue.enqueue(
                job_type="analyze",
                dedup_key="PtfBbQKRY9d6zAMC:1:Zarad",
                discord_user_id="user-1",
                discord_channel_id="chan-1",
            )
        )

    threads = [threading.Thread(target=_request) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert len(results) == 2
    job_ids = {r.job.job_id for r in results}  # type: ignore[attr-defined]
    assert len(job_ids) == 1  # both requests resolved to the SAME job
    assert sum(1 for r in results if r.deduped) == 1  # type: ignore[attr-defined]


def test_fifth_request_rejected_when_user_has_one_active_and_three_queued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs_module, "USER_COOLDOWN_S", 0.0)
    queue, _store = _queue(tmp_path)
    assert MAX_ACTIVE_JOBS_PER_USER == 1
    assert MAX_QUEUED_JOBS_PER_USER == 3

    # 4 distinct (different dedup_key) requests fill 1 active + 3 queued.
    for i in range(4):
        result = queue.enqueue(
            job_type="analyze",
            dedup_key=f"report{i}:1:Player{i}",
            discord_user_id="user-1",
            discord_channel_id="chan-1",
        )
        assert result.job is not None
    queue.claim_next(_full_budget())  # one of the 4 becomes "running" (the active one)

    fifth = queue.enqueue(
        job_type="analyze",
        dedup_key="report5:1:Player5",
        discord_user_id="user-1",
        discord_channel_id="chan-1",
    )

    assert fifth.job is None
    assert fifth.rejected_reason is not None
    assert "4" in fifth.rejected_reason


def test_cold_cohort_jobs_pause_below_reserve_while_analyze_jobs_continue(
    tmp_path: Path,
) -> None:
    queue, _store = _queue(tmp_path)
    queue.enqueue(
        job_type="build_cohort",
        dedup_key="cohort:3179:Warlock:Demonology:5:all",
        discord_user_id="user-1",
        discord_channel_id="chan-1",
    )
    queue.enqueue(
        job_type="analyze",
        dedup_key="report1:1:Player1",
        discord_user_id="user-2",
        discord_channel_id="chan-1",
    )

    # Below the 25% interactive reserve (900 of a 3600 limitPerHour) but
    # above the floor — note T0.3's real default floor (1000) is actually
    # *higher* than 25% of the account's real 3600 limit, so with the
    # literal default numbers there is no daylight between "below reserve"
    # and "below floor" for this account; a custom, lower floor is used
    # here purely to exercise the tiered logic on its own terms.
    below_reserve = BudgetStatus(points_remaining=700.0, limit_per_hour=3600.0, floor=500.0)
    claimed = queue.claim_next(below_reserve)

    assert claimed is not None
    assert claimed.job_type == "analyze"  # the cohort job was skipped, not claimed

    # Nothing else claimable: cohort job stays queued, no analyze jobs left.
    assert queue.claim_next(below_reserve) is None
    cohort_job = next(j for j in queue.list_active() if j.job_type == "build_cohort")
    assert cohort_job.status == "queued"


def test_claim_next_returns_none_below_the_absolute_floor(tmp_path: Path) -> None:
    queue, _store = _queue(tmp_path)
    queue.enqueue(
        job_type="analyze",
        dedup_key="report1:1:Player1",
        discord_user_id="user-1",
        discord_channel_id="chan-1",
    )
    below_floor = BudgetStatus(points_remaining=500.0, limit_per_hour=3600.0)
    assert queue.claim_next(below_floor) is None


def test_requeue_after_budget_exhaustion_makes_the_job_claimable_again(
    tmp_path: Path,
) -> None:
    """T1.8 §3: 'build-cohort interrompido por orçamento -> ... ao
    reprocessar, apenas os faltantes são baixados' — at the job-queue
    level, this means requeue() puts a running job back to queued so it
    can be claimed and resumed (the store-level cache guarantees the
    'only missing logs' part, tested in test_cohort_builder.py).
    """
    queue, _store = _queue(tmp_path)
    queue.enqueue(
        job_type="build_cohort",
        dedup_key="cohort:3179:Warlock:Demonology:5:all",
        discord_user_id="user-1",
        discord_channel_id="chan-1",
    )
    claimed = queue.claim_next(_full_budget())
    assert claimed is not None
    assert claimed.status == "running"

    queue.requeue(claimed.job_id)

    reclaimed = queue.claim_next(_full_budget())
    assert reclaimed is not None
    assert reclaimed.job_id == claimed.job_id
    assert reclaimed.status == "running"


def test_running_jobs_revert_to_queued_on_crash_recovery(tmp_path: Path) -> None:
    queue, _store = _queue(tmp_path)
    queue.enqueue(
        job_type="analyze",
        dedup_key="report1:1:Player1",
        discord_user_id="user-1",
        discord_channel_id="chan-1",
    )
    claimed = queue.claim_next(_full_budget())
    assert claimed is not None
    assert claimed.status == "running"

    n_reverted = queue.recover_from_crash()

    assert n_reverted == 1
    job = queue.get(claimed.job_id)
    assert job is not None
    assert job.status == "queued"
    assert job.started_at is None


# -- additional coverage --------------------------------------------------------


def test_cooldown_rejects_a_second_distinct_request_within_60s(tmp_path: Path) -> None:
    queue, _store = _queue(tmp_path)
    first = queue.enqueue(
        job_type="analyze",
        dedup_key="report1:1:Player1",
        discord_user_id="user-1",
        discord_channel_id="chan-1",
    )
    assert first.job is not None

    second = queue.enqueue(
        job_type="analyze",
        dedup_key="report2:1:Player2",  # different work, same user, too soon
        discord_user_id="user-1",
        discord_channel_id="chan-1",
    )

    assert second.job is None
    assert second.rejected_reason is not None
    assert "aguarde" in second.rejected_reason


def test_dedup_bypasses_cooldown(tmp_path: Path) -> None:
    queue, _store = _queue(tmp_path)
    first = queue.enqueue(
        job_type="analyze",
        dedup_key="report1:1:Player1",
        discord_user_id="user-1",
        discord_channel_id="chan-1",
    )
    second = queue.enqueue(
        job_type="analyze",
        dedup_key="report1:1:Player1",  # same work
        discord_user_id="user-1",
        discord_channel_id="chan-1",
    )

    assert second.deduped is True
    assert second.job is not None
    assert first.job is not None
    assert second.job.job_id == first.job.job_id


def test_max_concurrent_jobs_caps_running_count(tmp_path: Path) -> None:
    queue, _store = _queue(tmp_path)
    assert MAX_CONCURRENT_JOBS == 2
    for i in range(4):
        queue.enqueue(
            job_type="analyze",
            dedup_key=f"report{i}:1:Player{i}",
            discord_user_id=f"user-{i}",
            discord_channel_id="chan-1",
        )

    claimed = [queue.claim_next(_full_budget()) for _ in range(4)]
    n_claimed = sum(1 for c in claimed if c is not None)

    assert n_claimed == MAX_CONCURRENT_JOBS


def test_analyze_jobs_are_prioritized_over_build_cohort_jobs(tmp_path: Path) -> None:
    queue, _store = _queue(tmp_path)
    queue.enqueue(
        job_type="build_cohort",
        dedup_key="cohort:3179:Warlock:Demonology:5:all",
        discord_user_id="user-1",
        discord_channel_id="chan-1",
    )
    queue.enqueue(
        job_type="analyze",
        dedup_key="report1:1:Player1",
        discord_user_id="user-2",
        discord_channel_id="chan-1",
    )

    claimed = queue.claim_next(_full_budget())

    assert claimed is not None
    assert claimed.job_type == "analyze"  # claimed first despite being enqueued second


def test_get_returns_none_for_unknown_job_id(tmp_path: Path) -> None:
    queue, _store = _queue(tmp_path)
    assert queue.get("does-not-exist") is None


def test_mark_done_and_mark_failed_update_status(tmp_path: Path) -> None:
    queue, _store = _queue(tmp_path)
    result = queue.enqueue(
        job_type="analyze",
        dedup_key="report1:1:Player1",
        discord_user_id="user-1",
        discord_channel_id="chan-1",
    )
    assert result.job is not None

    queue.mark_done(result.job.job_id, report_path="/tmp/report.md")
    done = queue.get(result.job.job_id)
    assert done is not None
    assert done.status == "done"
    assert done.report_path == "/tmp/report.md"

    other = queue.enqueue(
        job_type="analyze",
        dedup_key="report2:1:Player2",
        discord_user_id="user-2",
        discord_channel_id="chan-1",
    )
    assert other.job is not None
    queue.mark_failed(other.job.job_id, error="algo deu errado")
    failed = queue.get(other.job.job_id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error == "algo deu errado"
