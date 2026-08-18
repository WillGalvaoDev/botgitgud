"""T1.8 — job execution: given an already-claimed Job, run it (analyze or
build_cohort) and record the outcome. Pure/synchronous — no asyncio, no
Discord — so it's testable without a live event loop or bot connection.
The async loop that claims jobs and calls this lives in
bot/discord_bot.py, Discord glue that (like every command handler in that
module already) stays outside unit-test coverage — see docs/desvios.md
D-22.

dedup_key doubles as the job's own parameters, parsed back out here:
- analyze:      "<report_code>:<fight_id>:<character_name>"
- build_cohort: "cohort:<encounter_id>:<class_name>:<spec_name>:<difficulty>:<bucket_s_or_all>"
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from botgitgud.analysis.cohort_builder import build_cohorts
from botgitgud.analysis.pipeline import AnalysisRequest, Deps, run_analysis
from botgitgud.bot.job_models import Job
from botgitgud.bot.jobs import JobQueue
from botgitgud.errors import BotGitGudError, RateLimitBudgetExceeded
from botgitgud.report.text import render_report

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class JobOutcome:
    job: Job
    ok: bool
    message: str
    requeued: bool = False


def run_claimed_job(queue: JobQueue, job: Job, deps: Deps) -> JobOutcome:
    """`job` must already be 'running' (i.e. returned by
    JobQueue.claim_next) — this only executes it and records the result.

    RateLimitBudgetExceeded mid-job is not a job failure: T1.8 §3 requires
    it go back to `queued` for after pointsResetIn, with whatever partial
    progress LogFetcher/Store already made kept (docs/desvios.md D-18) —
    never marked `failed`.
    """
    try:
        if job.job_type == "analyze":
            message = _run_analyze(job, deps)
        else:
            message = _run_build_cohort(job, deps)
    except RateLimitBudgetExceeded as e:
        log.info("worker.job_requeued_budget_exceeded", job_id=job.job_id, error=str(e))
        queue.requeue(job.job_id)
        return JobOutcome(job=job, ok=False, message=str(e), requeued=True)
    except BotGitGudError as e:
        log.warning("worker.job_failed", job_id=job.job_id, job_type=job.job_type, error=str(e))
        queue.mark_failed(job.job_id, error=str(e))
        return JobOutcome(job=job, ok=False, message=str(e))

    queue.mark_done(job.job_id)
    return JobOutcome(job=job, ok=True, message=message)


def _run_analyze(job: Job, deps: Deps) -> str:
    report_code, fight_id_s, character_name = job.dedup_key.split(":", 2)
    req = AnalysisRequest(
        report_code=report_code, fight_id=int(fight_id_s), character_name=character_name
    )
    # A worker job runs in a controlled, budgeted, fair background context
    # (unlike the interactive path, which never builds cold) — allowed to
    # do the full cohort fetch if the fast warm-profile lookup missed.
    result = run_analysis(req, deps, allow_cold_build=True)
    return render_report(
        result.header, result.comparisons, result.manifest, result.build_divergence
    )


def _run_build_cohort(job: Job, deps: Deps) -> str:
    _prefix, encounter_s, class_name, spec_name, difficulty_s, bucket_s = job.dedup_key.split(
        ":", 5
    )
    duration_bucket_s = None if bucket_s == "all" else float(bucket_s)
    results = build_cohorts(
        deps,
        encounter_id=int(encounter_s),
        class_name=class_name,
        spec_name=spec_name,
        difficulty=int(difficulty_s),
        duration_bucket_s=duration_bucket_s,
    )
    if not results:
        return "Nenhuma coorte pôde ser construída (candidatos insuficientes em todo bucket)."
    lines = [
        f"bucket {r.bucket_id} [{r.duration_min_s:.0f}s-{r.duration_max_s:.0f}s]: "
        f"{r.n_members} membros"
        for r in results
    ]
    return f"{len(results)} coorte(s) construída(s):\n" + "\n".join(lines)
