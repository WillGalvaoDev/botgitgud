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

import structlog

from botgitgud.analysis.cohort_builder import build_cohorts
from botgitgud.analysis.pipeline import AnalysisRequest, AnalysisResult, Deps, run_analysis
from botgitgud.bot.analysis_runs import record_analysis_result, track_analysis
from botgitgud.bot.benchmark_job import run_benchmark_build_job
from botgitgud.bot.benchmark_trigger import maybe_enqueue_benchmark_build
from botgitgud.bot.job_models import Job, JobOutcome
from botgitgud.bot.jobs import JobQueue
from botgitgud.bot.report_store import ReportPersistenceError, persist_report
from botgitgud.errors import BotGitGudError, CohortDeferredBudget, RateLimitBudgetExceeded
from botgitgud.report.render import render_analysis

log = structlog.get_logger(__name__)


def run_claimed_job(queue: JobQueue, job: Job, deps: Deps) -> JobOutcome:
    """`job` must already be 'running' (i.e. returned by
    JobQueue.claim_next) — this only executes it and records the result.

    RateLimitBudgetExceeded mid-job is not a job failure: T1.8 §3 requires
    it go back to `queued` for after pointsResetIn, with whatever partial
    progress LogFetcher/Store already made kept (docs/desvios.md D-18) —
    never marked `failed`.
    """
    if job.job_type == "benchmark_build":
        # EB.5: fluxo de mapeamento de estado inteiramente distinto
        # (READY/DEFERRED_BUDGET/NO_PROGRESS/FAILED de
        # analysis/benchmark_builder.py, não CohortDeferredBudget) — vive em
        # bot/benchmark_job.py, não misturado no try/except abaixo, que é
        # específico do par analyze/build_cohort.
        return run_benchmark_build_job(queue, job, deps)

    report_path: str | None = None
    analysis: AnalysisResult | None = None
    try:
        if job.job_type == "analyze":
            message, html_report, analysis = _run_analyze(job, deps)
            # RC.1/RC.11: o artefato vira arquivo ANTES de qualquer tentativa de
            # entrega. Se isto falhar, o job falha como erro de producao do
            # artifact — nunca seguimos para o Discord com um anexo inexistente.
            report_path = str(persist_report(deps.settings.data_dir, job.job_id, html_report))
            log.info("worker.report_persisted", job_id=job.job_id, report_path=report_path)
        else:
            message = _run_build_cohort(job, deps)
            html_report = None
    except ReportPersistenceError as e:
        log.error("worker.report_persistence_failed", job_id=job.job_id, error=str(e))
        queue.mark_failed(job.job_id, error=str(e))
        return JobOutcome(job=job, ok=False, message=str(e))
    except RateLimitBudgetExceeded as e:
        log.info("worker.job_requeued_budget_exceeded", job_id=job.job_id, error=str(e))
        queue.requeue(job.job_id)
        return JobOutcome(job=job, ok=False, message=str(e), requeued=True)
    except CohortDeferredBudget as e:
        # B2 — ESTA clausula precede `BotGitGudError` de proposito. Antes dela o
        # adiamento caia no ramo generico e virava `mark_failed`, contradizendo
        # a mensagem dada ao usuario e descartando o pedido junto com o
        # progresso ja pago em pontos de API.
        return _defer_job(queue, job, e, deps)
    except BotGitGudError as e:
        log.warning("worker.job_failed", job_id=job.job_id, job_type=job.job_type, error=str(e))
        queue.mark_failed(job.job_id, error=str(e))
        return JobOutcome(job=job, ok=False, message=str(e))

    queue.mark_done(job.job_id, report_path=report_path)
    log.info("worker.analysis_completed", job_id=job.job_id, job_type=job.job_type)
    if analysis is not None:
        # EB.6: só DEPOIS de o artefato estar persistido e o job marcado
        # `done` — o relatório nunca espera por isto, e uma falha aqui não
        # pode reverter nada (o job já está concluído).
        maybe_enqueue_benchmark_build(deps=deps, queue=queue, result=analysis, source="worker")
    return JobOutcome(
        job=job,
        ok=True,
        message=message,
        html_report=html_report,
        report_path=report_path,
    )


def _defer_job(queue: JobQueue, job: Job, error: CohortDeferredBudget, deps: Deps) -> JobOutcome:
    """Agenda a retomada com o dado REAL de reset da janela quando existir.

    `pointsResetIn` vem junto do `rateLimitData` que o cliente ja consulta, entao
    saber a hora certa nao custa chamada extra. Sem esse dado a espera cai no
    padrao conservador — nunca um retry apertado, porque o orcamento so melhora
    com o reset da janela.
    """
    retry_after = error.retry_after_s
    if retry_after is None or retry_after <= 0:
        retry_after = deps.settings.cold_build_defer_retry_s
    until = queue.defer(job.job_id, retry_after_s=retry_after, reason=str(error))
    log.info(
        "worker.job_deferred_budget",
        job_id=job.job_id,
        cohort_id=error.cohort_id,
        planned=error.planned,
        completed=error.completed,
        retry_after_s=round(retry_after, 1),
        deferred_until=until.isoformat(),
    )
    return JobOutcome(
        job=job,
        ok=False,
        message=str(error),
        deferred=True,
        deferred_until=until.isoformat(),
    )


def _record_cold_progress(run: object, error: CohortDeferredBudget) -> None:
    run.cold_build_started = True  # type: ignore[attr-defined]
    run.cold_build_state = "deferred_budget"  # type: ignore[attr-defined]
    run.cohort_id = error.cohort_id  # type: ignore[attr-defined]
    run.cold_build_planned = error.planned  # type: ignore[attr-defined]
    run.cold_build_completed = error.completed  # type: ignore[attr-defined]
    run.cold_build_remaining = error.remaining  # type: ignore[attr-defined]
    run.final_status = "deferred_budget"  # type: ignore[attr-defined]


def _run_analyze(job: Job, deps: Deps) -> tuple[str, str, AnalysisResult]:
    report_code, fight_id_s, character_name = job.dedup_key.split(":", 2)
    req = AnalysisRequest(
        report_code=report_code, fight_id=int(fight_id_s), character_name=character_name
    )
    # O caminho da fila e o CARO — ate 100 referencias frias. Sem telemetria
    # aqui, a unica prova do que ele gastou morria em stdout; e um job adiado
    # nao deixava rastro nenhum de quanto ja tinha avancado.
    with track_analysis(
        deps.settings.data_dir,
        budget=deps.client,
        player=character_name,
        report_code=report_code,
        fight_id=int(fight_id_s),
        job_id=job.job_id,
    ) as run:
        # A worker job runs in a controlled, budgeted, fair background context
        # (unlike the interactive path, which never builds cold) — allowed to
        # do the full cohort fetch if the fast warm-profile lookup missed.
        try:
            result = run_analysis(req, deps, allow_cold_build=True, job_id=job.job_id)
        except CohortDeferredBudget as e:
            _record_cold_progress(run, e)
            raise
        # Fato autoritativo vindo do pipeline: um build frio bem-sucedido nao
        # e caminho quente, embora produza exatamente o mesmo relatorio.
        run.cold_build_started = getattr(result, "cold_build_performed", False)
        run.cold_build_state = "ready" if run.cold_build_started else None
        run.hot_path = not run.cold_build_started
        record_analysis_result(run, result)
        # RP.3: nunca renderiza a partir do `AnalysisResult` cru — o
        # `ReportContract` (RP.0) e seu guarda execution-only são
        # obrigatórios, e `render_analysis` é o único jeito de chegar aos
        # renderizadores por este caminho.
        rendered = render_analysis(result)
        return rendered.summary, rendered.html, result


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
