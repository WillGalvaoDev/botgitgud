from __future__ import annotations

import threading
from pathlib import Path

import pytest

import botgitgud.bot.jobs as jobs_module
from botgitgud.bot.job_models import BudgetStatus, Job, now_utc_naive
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


# -- B2: `deferred_budget` e um estado proprio, com hora marcada -------------------


def _enqueue(queue: JobQueue, *, dedup_key: str = "R:1:Zarad", user: str = "user-1") -> str:
    result = queue.enqueue(
        job_type="analyze",
        dedup_key=dedup_key,
        discord_user_id=user,
        discord_channel_id="chan-1",
    )
    assert result.job is not None
    return result.job.job_id


def test_defer_is_not_a_terminal_failure(tmp_path: Path) -> None:
    queue, store = _queue(tmp_path)
    job_id = _enqueue(queue)
    assert queue.claim_next(_full_budget()) is not None

    queue.defer(job_id, retry_after_s=0.0, reason="orçamento reservado")

    job = queue.get(job_id)
    assert job is not None
    assert job.status == "deferred_budget"
    assert job.finished_at is None
    assert job.error is None
    assert job.defer_reason == "orçamento reservado"
    assert job.defer_count == 1
    store.close()


def test_a_deferred_job_is_not_claimable_before_its_scheduled_retry(tmp_path: Path) -> None:
    """Sem `deferred_until` o worker giraria em loop apertado sobre um
    orçamento que só melhora no reset da janela.
    """
    queue, store = _queue(tmp_path)
    job_id = _enqueue(queue)
    assert queue.claim_next(_full_budget()) is not None
    queue.defer(job_id, retry_after_s=3600.0, reason="orçamento reservado")

    assert queue.claim_next(_full_budget()) is None
    store.close()


def test_a_deferred_job_becomes_claimable_once_the_wait_has_elapsed(tmp_path: Path) -> None:
    queue, store = _queue(tmp_path)
    job_id = _enqueue(queue)
    assert queue.claim_next(_full_budget()) is not None
    queue.defer(job_id, retry_after_s=0.0, reason="orçamento reservado")

    resumed = queue.claim_next(_full_budget())

    assert resumed is not None
    assert resumed.job_id == job_id
    assert resumed.status == "running"
    assert resumed.defer_count == 1  # nunca zerado por uma retomada
    store.close()


def test_repeating_the_request_while_deferred_reuses_the_same_job(tmp_path: Path) -> None:
    queue, store = _queue(tmp_path)
    job_id = _enqueue(queue)
    assert queue.claim_next(_full_budget()) is not None
    queue.defer(job_id, retry_after_s=3600.0, reason="orçamento reservado")

    repeated = queue.enqueue(
        job_type="analyze",
        dedup_key="R:1:Zarad",
        discord_user_id="user-2",
        discord_channel_id="chan-2",
    )

    assert repeated.deduped is True
    assert repeated.job is not None and repeated.job.job_id == job_id
    store.close()


def test_a_deferred_job_still_counts_as_active_work(tmp_path: Path) -> None:
    queue, store = _queue(tmp_path)
    job_id = _enqueue(queue)
    assert queue.claim_next(_full_budget()) is not None
    queue.defer(job_id, retry_after_s=3600.0, reason="orçamento reservado")

    active = queue.list_active()

    assert [j.job_id for j in active] == [job_id]
    assert active[0].is_deferred is True
    store.close()


def test_repeated_deferrals_accumulate_instead_of_resetting(tmp_path: Path) -> None:
    queue, store = _queue(tmp_path)
    job_id = _enqueue(queue)
    for _ in range(3):
        assert queue.claim_next(_full_budget()) is not None
        queue.defer(job_id, retry_after_s=0.0, reason="orçamento reservado")

    job = queue.get(job_id)
    assert job is not None
    assert job.defer_count == 3
    store.close()


# -- B3-fix (incidente real do soak): guard do worker precisa concordar com
# claim_next() sobre o que e "trabalho pendente" -----------------------------


def _job_with_status(
    *,
    status: str,
    deferred_until: object = None,
) -> Job:

    return Job(
        job_id="job-x",
        job_type="analyze",
        dedup_key="R:1:P",
        discord_user_id="u",
        discord_channel_id="c",
        status=status,  # type: ignore[arg-type]
        created_at=now_utc_naive(),
        started_at=None,
        finished_at=None,
        error=None,
        report_path=None,
        deferred_until=deferred_until,  # type: ignore[arg-type]
    )


def test_is_claimable_true_for_queued() -> None:
    assert _job_with_status(status="queued").is_claimable() is True


def test_is_claimable_true_for_deferred_budget_past_due() -> None:
    from datetime import timedelta

    past = now_utc_naive() - timedelta(seconds=1)
    assert _job_with_status(status="deferred_budget", deferred_until=past).is_claimable() is True


def test_is_claimable_false_for_deferred_budget_in_the_future() -> None:
    from datetime import timedelta

    future = now_utc_naive() + timedelta(hours=1)
    job = _job_with_status(status="deferred_budget", deferred_until=future)
    assert job.is_claimable() is False


def test_is_claimable_true_for_deferred_budget_with_no_deferred_until() -> None:
    """`deferred_until=None` significa "sem prazo conhecido" — nunca bloqueia
    a retomada indefinidamente (mesma regra de `deferral_elapsed`).
    """
    assert _job_with_status(status="deferred_budget", deferred_until=None).is_claimable() is True


@pytest.mark.parametrize("status", ["done", "failed", "running", "cancelled"])
def test_is_claimable_false_for_terminal_or_running_status(status: str) -> None:
    assert _job_with_status(status=status).is_claimable() is False


def test_is_claimable_accepts_an_explicit_now_for_deterministic_tests() -> None:
    from datetime import timedelta

    deferred_until = now_utc_naive() + timedelta(seconds=10)
    job = _job_with_status(status="deferred_budget", deferred_until=deferred_until)
    assert job.is_claimable(now=deferred_until - timedelta(seconds=1)) is False
    assert job.is_claimable(now=deferred_until) is True
    assert job.is_claimable(now=deferred_until + timedelta(seconds=1)) is True


# -- coerencia: Job.is_claimable() precisa concordar com claim_next() de verdade ----


def test_is_claimable_agrees_with_claim_next_for_an_expired_deferral(tmp_path: Path) -> None:
    queue, store = _queue(tmp_path)
    job_id = _enqueue(queue)
    assert queue.claim_next(_full_budget()) is not None
    queue.defer(job_id, retry_after_s=0.0, reason="orçamento reservado")

    stored = queue.get(job_id)
    assert stored is not None
    assert stored.is_claimable() is True  # a MESMA previsao que claim_next() confirma abaixo

    resumed = queue.claim_next(_full_budget())
    assert resumed is not None and resumed.job_id == job_id
    store.close()


def test_is_claimable_agrees_with_claim_next_for_a_future_deferral(tmp_path: Path) -> None:
    queue, store = _queue(tmp_path)
    job_id = _enqueue(queue)
    assert queue.claim_next(_full_budget()) is not None
    queue.defer(job_id, retry_after_s=3600.0, reason="orçamento reservado")

    stored = queue.get(job_id)
    assert stored is not None
    assert stored.is_claimable() is False  # a MESMA previsao que claim_next() confirma abaixo

    assert queue.claim_next(_full_budget()) is None
    store.close()


def test_is_claimable_agrees_with_claim_next_for_done_and_failed(tmp_path: Path) -> None:
    queue, store = _queue(tmp_path)
    done_id = _enqueue(queue, dedup_key="R:1:Done", user="user-done")
    claimed = queue.claim_next(_full_budget())
    assert claimed is not None
    queue.mark_done(done_id)

    failed_id = _enqueue(queue, dedup_key="R:1:Failed", user="user-failed")
    claimed2 = queue.claim_next(_full_budget())
    assert claimed2 is not None
    queue.mark_failed(failed_id, error="boom")

    for job_id in (done_id, failed_id):
        stored = queue.get(job_id)
        assert stored is not None
        assert stored.is_claimable() is False
    assert queue.claim_next(_full_budget()) is None
    store.close()
