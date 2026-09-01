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
from botgitgud.errors import (
    COLD_COHORT_NO_PROGRESS,
    BotGitGudError,
    CohortDeferredBudget,
    RateLimitBudgetExceeded,
)
from botgitgud.report.render import contract_for, render_analysis

log = structlog.get_logger(__name__)

# CL.0 — teto persistente de defers para cold cohort (`CohortDeferredBudget`,
# analyze e build_cohort — os dois únicos job_types que passam por
# `_defer_job`). Sem isto, um cold cohort com candidatos permanentemente
# inatingíveis (a "Anonymous" que a WCL às vezes anonimiza) deferia para
# sempre: `deferred_until` vence, `Job.is_claimable()` já garante a retomada
# (B2), o worker reclama o MESMO job, tenta de novo, bate no MESMO
# NO_PROGRESS estrutural, defere de novo — sem nunca virar READY nem
# `failed`. Incidente real: job `2b3a1091d00c...` chegou a `defer_count=2`
# reproduzindo exatamente isso.
#
# Deliberadamente MENOR que `bot/benchmark_job.py`'s `_MAX_CONSECUTIVE_
# DEFERS` (50) — as duas semânticas não são a mesma. Um benchmark_build
# retoma de um checkpoint por-candidato (`benchmark_build_progress`,
# EB.4): cada defer é barato, e um candidato permanentemente ruim vira
# `failed` sozinho depois de `MAX_CANDIDATE_ATTEMPTS`, encolhendo `pending`
# até o build progredir de verdade. Um cold cohort não tem checkpoint
# persistido até ficar READY (parcial nunca é READY, T1.6/B1) — cada defer
# aqui refaz a descoberta do leaderboard inteira do zero
# (`fetch_ranking_candidates`, dentro de `_advance_cold_cohort`/
# `build_cohorts`), e uma retomada real já gastou ~1700 pontos WCL só
# reconstruindo esse progresso. 50 defers deste tipo poderiam custar dezenas
# de milhares de pontos e semanas de janelas `deferred_until` perseguindo
# uma coorte que, por ter candidatos estruturalmente inalcançáveis, nunca
# vai completar. O job real citado acima já esgotou 2 defers — ambos por
# NO_PROGRESS, nunca budget puro — e o cold build da MESMA coorte, quando o
# orçamento coopera, historicamente completou 99/100 candidatos numa única
# janela: poucas tentativas bastam para o caso genuinamente transitório de
# escassez de orçamento, e cortam um loop estrutural em horas, não semanas.
MAX_COLD_COHORT_DEFERS = 3


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

    # CL.0-hardening: um cold cohort cujo ÚLTIMO defer persistido já foi
    # NO_PROGRESS estrutural, E que está na borda do teto (a PRÓXIMA
    # execução seria justamente a que `_defer_job` terminaria de qualquer
    # forma), não precisa reexecutar o pipeline inteiro só para redescobrir
    # a MESMA coisa — especialmente caro para cold cohort, que não tem
    # checkpoint persistido até READY (cada retomada refaz a descoberta do
    # leaderboard do zero; uma retomada real chegou a gastar ~1700 pontos
    # WCL só para reconfirmar NO_PROGRESS). Zero chamada ao pipeline, zero
    # WCL — o job vai direto a `failed`, sem passar por `claim` de novo.
    #
    # Deliberadamente conservador: só dispara quando o sinal persistido é o
    # código estruturado exato (nunca a mensagem livre de um chamador
    # antigo, nunca inferido) E o job já está no MESMO ponto em que
    # `_defer_job` terminaria de qualquer forma — o guard nunca antecipa a
    # terminação para mais cedo do que o teto já definiria, só evita
    # reexecutar quando o resultado já é conhecido. Um job cujo último
    # defer foi orçamento (`defer_reason` ausente ou diferente) continua
    # tendo direito a toda a janela de retries normalmente.
    if (
        job.defer_reason == COLD_COHORT_NO_PROGRESS
        and job.defer_count + 1 >= MAX_COLD_COHORT_DEFERS
    ):
        message = (
            f"cold cohort pré-terminado: {job.defer_count} adiamento(s) consecutivo(s) já "
            "demonstraram NO_PROGRESS estrutural — nenhuma nova execução foi necessária "
            "para confirmar."
        )
        queue.mark_failed(job.job_id, error=message)
        log.error(
            "worker.cold_cohort_pre_terminated",
            job_id=job.job_id,
            job_type=job.job_type,
            defer_count=job.defer_count,
            max_defers=MAX_COLD_COHORT_DEFERS,
            reason="cold_cohort_no_progress_pre_terminated",
        )
        return JobOutcome(job=job, ok=False, message=message)

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
    # CL.5: o MESMO construtor canônico (RP.0) que render_analysis já usou
    # dentro de _run_analyze — recomputar é barato/puro (nenhum I/O), e
    # evita alargar a assinatura de _run_analyze só para carregar o
    # contrato de volta através de mais uma camada.
    contract = contract_for(analysis) if analysis is not None else None
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
        report_contract=contract,
    )


def _defer_job(queue: JobQueue, job: Job, error: CohortDeferredBudget, deps: Deps) -> JobOutcome:
    """Agenda a retomada com o dado REAL de reset da janela quando existir.

    `pointsResetIn` vem junto do `rateLimitData` que o cliente ja consulta, entao
    saber a hora certa nao custa chamada extra. Sem esse dado a espera cai no
    padrao conservador — nunca um retry apertado, porque o orcamento so melhora
    com o reset da janela.

    CL.0: `defer_count` já persistido do próprio `job` (sobrevive restart —
    é a mesma coluna que B2 já usa) é o teto de EXECUÇÕES. Trata budget
    puro e NO_PROGRESS estrutural igual para efeito de QUANTAS vezes um
    job pode deferir — mesma simplificação que `bot/benchmark_job.py`'s
    `_defer` já adota deliberadamente para o mesmo par de causas — não
    transforma escassez transitória em falha prematura (o cap é generoso
    o bastante para o caso real de budget: a MESMA coorte completou 99/100
    numa única janela quando o orçamento cooperou), e corta um bloqueio
    estrutural antes que ele vire um loop sem fim.

    CL.0-hardening: `error.defer_reason` (COLD_COHORT_BUDGET/
    COLD_COHORT_NO_PROGRESS, `errors.py`) é persistido em vez da mensagem
    livre — é esse código estruturado que o guard de pré-terminação no
    topo de `run_claimed_job` lê depois, nunca a string humana. Um
    `defer_reason=None` (chamador que ainda não popula a causa) cai no
    texto legado — nunca falso-negativo, só perde o atalho.
    """
    if job.defer_count + 1 >= MAX_COLD_COHORT_DEFERS:
        message = (
            f"cold cohort {error.cohort_id} excedeu {MAX_COLD_COHORT_DEFERS} adiamentos "
            f"consecutivos (última causa: {error})"
        )
        queue.mark_failed(job.job_id, error=message)
        log.error(
            "worker.cold_cohort_retry_exhausted",
            job_id=job.job_id,
            job_type=job.job_type,
            cohort_id=error.cohort_id,
            defer_count=job.defer_count,
            max_defers=MAX_COLD_COHORT_DEFERS,
            planned=error.planned,
            completed=error.completed,
            reason="cold_cohort_retry_exhausted",
        )
        return JobOutcome(job=job, ok=False, message=message)

    retry_after = error.retry_after_s
    if retry_after is None or retry_after <= 0:
        retry_after = deps.settings.cold_build_defer_retry_s
    until = queue.defer(
        job.job_id, retry_after_s=retry_after, reason=error.defer_reason or str(error)
    )
    log.info(
        "worker.job_deferred_budget",
        job_id=job.job_id,
        cohort_id=error.cohort_id,
        planned=error.planned,
        completed=error.completed,
        defer_reason=error.defer_reason,
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
