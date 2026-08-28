"""EB.5 — Encounter Benchmark as a real, low-priority job of the SAME
`JobQueue` `!analisar`/`build_cohort` already use (docs/desvios.md D-19: one
warehouse, one lock, one queue — never a second fila). This module owns two
things `bot/jobs.py`/`bot/worker.py` don't know about: what a
`benchmark_build` job's `payload_json` means, and how `analysis/
benchmark_builder.py`'s 4 states (READY/DEFERRED_BUDGET/NO_PROGRESS/FAILED)
map onto the queue's own `JobStatus` lifecycle.

No Discord anywhere in this file (EB.5 ticket: "É job interno"). No cost
model (`analysis/benchmark_build_budget.py` already owns that — this module
only calls `advance_benchmark_build` and obeys its answer). No candidate
re-discovery on resume: once `benchmark_build_progress` has rows for a
`benchmark_id` (EB.4), those rows ARE the candidate plan — the payload's own
candidate list is only ever read on the very first claim.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

import structlog

from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget
from botgitgud.analysis.benchmark_build_budget import affordable_fights
from botgitgud.analysis.benchmark_build_progress import BenchmarkBuildProgressStore
from botgitgud.analysis.benchmark_builder import (
    BenchmarkBuildResult,
    BenchmarkBuildState,
    advance_benchmark_build,
)
from botgitgud.analysis.benchmark_store import BenchmarkStore
from botgitgud.analysis.benchmark_store_models import (
    DEFAULT_FRESHNESS_POLICY,
    BenchmarkFreshnessPolicy,
)
from botgitgud.analysis.pipeline import Deps
from botgitgud.bot.job_models import Job, JobOutcome
from botgitgud.bot.jobs import JobQueue
from botgitgud.domain.models import RankingCandidate

log = structlog.get_logger(__name__)

# Job interno — nunca um usuário/canal Discord real. Prefixo não-numérico de
# propósito: um discord_channel_id real é sempre um snowflake numérico, então
# qualquer tentativa acidental de notificar por Discord (ex.: um `int()`
# nesta string) falha ruidosamente em vez de mandar mensagem para o canal
# errado.
SYSTEM_ACTOR_ID = "system:benchmark_build"


def _system_user_id(target: EncounterBenchmarkTarget) -> str:
    """Um "usuário" sintético POR benchmark_id, não um único global.

    `JobQueue.enqueue()`'s cooldown/cota (USER_COOLDOWN_S, MAX_QUEUED_
    JOBS_PER_USER) é por `discord_user_id` — correto para um humano
    reenviando `!analisar`, mas usar UM `SYSTEM_ACTOR_ID` compartilhado
    para todo benchmark faria pedir dois benchmarks DIFERENTES (specs ou
    encontros distintos) em sequência esbarrar no cooldown de 60s um do
    outro, sem relação alguma com o motivo do cooldown existir. Por
    benchmark_id, a cota só se aplica a pedidos repetidos do MESMO
    benchmark — já redundante com o dedup por `dedup_key`, e inofensiva.
    """
    return f"{SYSTEM_ACTOR_ID}:{target.benchmark_id}"


# Teto pequeno de lotes por reivindicação de job — nunca deixa um build de
# baixa prioridade monopolizar o worker sequencial por múltiplas janelas
# internas de uma vez (EB.5 §"hot path preemption"). O checkpoint de EB.4
# garante que isto é só uma questão de throughput, nunca de correção.
#
# Precisa ser >= 2, não 1: `advance_benchmark_build` só reconhece NO_PROGRESS
# reavaliando `untried_fights` no TOPO do laço, na iteração seguinte à que
# tentou o último fight disponível. Com teto 1 o laço nunca chega a essa
# segunda iteração — toda falha transitória seria rotulada DEFERRED_BUDGET
# pelo teto de lotes antes de o builder conseguir dizer "não há mais nada
# para tentar agora". Com 2, o pior caso ainda é pequeno e limitado (no
# máximo 2 x benchmark_build_chunk_fights fights por reivindicação).
_MAX_BATCHES_PER_CLAIM = 2

# Segurança contra defer infinito: mesmo sendo matematicamente impossível
# NO_PROGRESS se repetir para sempre (MAX_CANDIDATE_ATTEMPTS de EB.4 converte
# candidatos retentáveis em falha permanente, encolhendo `pending` até
# esvaziar), um teto explícito é barato e documenta a intenção — "não crie
# loop infinito" nunca deve depender só de uma prova indireta.
_MAX_CONSECUTIVE_DEFERS = 50

_PAYLOAD_SCHEMA_VERSION = 1


class BenchmarkJobPayloadError(ValueError):
    """`payload_json` de um job `benchmark_build` não pôde ser decodificado
    — falha fechada (mesma convenção de EB.1/EB.2/EB.3), nunca um build
    parcial/adivinhado.
    """


def dedup_key_for(target: EncounterBenchmarkTarget) -> str:
    """`benchmark_id` (EB.1) já É a identidade completa — reusado como
    dedup_key inteiro, então o dedup ATÔMICO que `JobQueue.enqueue()` já
    faz (mesmo lock, mesma checagem de `dedup_key` ativo) é o single-flight
    por benchmark_id, sem nenhum lock paralelo novo.
    """
    return f"benchmark:{target.benchmark_id}"


def _encode_payload(
    target: EncounterBenchmarkTarget,
    policy: BenchmarkPolicy,
    candidates: Sequence[RankingCandidate],
) -> str:
    payload = {
        "schema_version": _PAYLOAD_SCHEMA_VERSION,
        "target": target.to_dict(),
        "policy": policy.to_dict(),
        "candidates": [
            {
                "report_code": c.report_code,
                "fight_id": c.fight_id,
                "player_name": c.player_name,
                "duration_s": c.duration_s,
            }
            for c in candidates
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class _DecodedPayload:
    target: EncounterBenchmarkTarget
    policy: BenchmarkPolicy
    candidates: tuple[RankingCandidate, ...]


def _decode_payload(raw: str | None) -> _DecodedPayload:
    if not raw:
        raise BenchmarkJobPayloadError("job benchmark_build sem payload_json")
    try:
        data = json.loads(raw)
        candidates = tuple(
            RankingCandidate(
                report_code=c["report_code"],
                fight_id=c["fight_id"],
                player_name=c["player_name"],
                duration_s=c["duration_s"],
            )
            for c in data["candidates"]
        )
        return _DecodedPayload(
            target=EncounterBenchmarkTarget.from_dict(data["target"]),
            policy=BenchmarkPolicy.from_dict(data["policy"]),
            candidates=candidates,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BenchmarkJobPayloadError(f"payload_json inválido: {exc}") from exc


# -- enqueue-side: ensure_benchmark_job ------------------------------------------


@dataclass(frozen=True, slots=True)
class EnsureBenchmarkJobResult:
    job: Job | None
    created: bool
    reason: str  # "created" | "already_active" | "fresh" | "rejected"


def ensure_benchmark_job(
    *,
    deps: Deps,
    queue: JobQueue,
    target: EncounterBenchmarkTarget,
    policy: BenchmarkPolicy,
    candidates: Sequence[RankingCandidate],
    current_population_size: int,
    freshness_policy: BenchmarkFreshnessPolicy = DEFAULT_FRESHNESS_POLICY,
) -> EnsureBenchmarkJobResult:
    """Único ponto de entrada para pedir um benchmark — NUNCA constrói nada
    diretamente. Três desfechos possíveis, cada um com telemetria própria:

    1. `evaluate_benchmark_freshness` (EB.3) diz `fresh` -> nenhum job,
       nenhum trabalho inútil.
    2. já existe um job ativo com o MESMO `dedup_key` (mesmo `benchmark_id`)
       -> devolve o job existente, não cria um segundo (single-flight).
    3. caso contrário -> enfileira um `benchmark_build` novo, prioridade
       mais baixa da fila (jobs.py's `_JOB_TYPE_PRIORITY`).

    Nenhum gatilho automático/periódico aqui — só a função. Quem decide
    QUANDO chamar isto é responsabilidade de uma tarefa futura.
    """
    benchmark_store = BenchmarkStore(deps.store)
    freshness = benchmark_store.evaluate_benchmark_freshness(
        target=target,
        policy=policy,
        current_population_size=current_population_size,
        freshness_policy=freshness_policy,
    )
    if freshness.status == "fresh":
        log.info(
            "benchmark_job.skip_fresh",
            benchmark_id=target.benchmark_id,
            age_days=freshness.age_days,
        )
        return EnsureBenchmarkJobResult(job=None, created=False, reason="fresh")

    result = queue.enqueue(
        job_type="benchmark_build",
        dedup_key=dedup_key_for(target),
        discord_user_id=_system_user_id(target),
        discord_channel_id=SYSTEM_ACTOR_ID,
        payload_json=_encode_payload(target, policy, candidates),
    )
    if result.job is None:
        log.warning(
            "benchmark_job.enqueue_rejected",
            benchmark_id=target.benchmark_id,
            reason=result.rejected_reason,
        )
        return EnsureBenchmarkJobResult(
            job=None, created=False, reason=result.rejected_reason or "rejected"
        )
    if result.deduped:
        log.info(
            "benchmark_job.already_active",
            benchmark_id=target.benchmark_id,
            job_id=result.job.job_id,
            status=result.job.status,
        )
        return EnsureBenchmarkJobResult(job=result.job, created=False, reason="already_active")

    log.info(
        "benchmark_job_enqueued",
        benchmark_id=target.benchmark_id,
        job_id=result.job.job_id,
        stale_reasons=list(freshness.reasons),
        candidates=len(candidates),
    )
    return EnsureBenchmarkJobResult(job=result.job, created=True, reason="created")


# -- execution-side: run_benchmark_build_job ---------------------------------------


def run_benchmark_build_job(queue: JobQueue, job: Job, deps: Deps) -> JobOutcome:
    """Chamado por `bot/worker.py`'s `run_claimed_job` para
    `job.job_type == "benchmark_build"`. `job` já está `running` (veio de
    `claim_next`). Nunca reimplementa `advance_benchmark_build` — só decodifica
    o payload, decide de onde vêm os candidatos (payload na 1ª vez, checkpoint
    depois) e mapeia o resultado para uma transição de `JobQueue`.
    """
    try:
        payload = _decode_payload(job.payload_json)
    except BenchmarkJobPayloadError as e:
        log.error("benchmark_job_failed", job_id=job.job_id, error=str(e), reason="bad_payload")
        queue.mark_failed(job.job_id, error=str(e))
        return JobOutcome(job=job, ok=False, message=str(e))

    target, policy = payload.target, payload.policy
    progress_store = BenchmarkBuildProgressStore(deps.store)
    benchmark_store = BenchmarkStore(deps.store)

    # Checkpoint (EB.4) é a fonte de verdade a partir da 2a reivindicação —
    # nunca refaz discovery, nunca depende do payload continuar em sincronia
    # com o progresso real.
    existing_rows = progress_store.read_progress(target.benchmark_id)
    candidates: Sequence[RankingCandidate] = (
        tuple(
            RankingCandidate(r.report_code, r.fight_id, r.player_name, r.duration_s or 0.0)
            for r in existing_rows
        )
        if existing_rows
        else payload.candidates
    )

    log.info(
        "job.resumed" if job.defer_count else "job.started",
        job_id=job.job_id,
        job_type="benchmark_build",
        benchmark_id=target.benchmark_id,
        defer_count=job.defer_count,
        candidates=len(candidates),
    )
    log.info(
        "benchmark_job_started",
        job_id=job.job_id,
        benchmark_id=target.benchmark_id,
        defer_count=job.defer_count,
        planned=len(candidates),
    )

    try:
        result = advance_benchmark_build(
            query_fn=deps.client.query,
            client=deps.client,
            log_store=deps.store,
            progress_store=progress_store,
            benchmark_store=benchmark_store,
            settings=deps.settings,
            target=target,
            policy=policy,
            candidates=candidates,
            max_batches=_MAX_BATCHES_PER_CLAIM,
        )
    except ValueError as e:
        # EncounterBenchmarkAggregationError/BenchmarkPolicyError/
        # EncounterBenchmarkTargetError/EncounterBenchmarkCorruptPayloadError
        # (analysis/benchmark*.py) sao ValueError, nunca BotGitGudError (a
        # mesma convencao de EB.1-EB.3) — falha estrutural, nunca retentavel
        # sozinha.
        log.error(
            "benchmark_job_failed",
            job_id=job.job_id,
            benchmark_id=target.benchmark_id,
            error=str(e),
            reason="builder_error",
        )
        queue.mark_failed(job.job_id, error=str(e))
        return JobOutcome(job=job, ok=False, message=str(e))
    # RateLimitBudgetExceeded (piso duro da propria WCL, atravessando
    # advance_benchmark_build sem ser capturado — mesmo contrato de
    # cohort_increment.py) propaga daqui e cai no `except RateLimitBudgetExceeded`
    # generico de run_claimed_job, que ja sabe fazer `queue.requeue()`.

    return _map_result(queue, job, deps, result)


def _map_result(queue: JobQueue, job: Job, deps: Deps, result: BenchmarkBuildResult) -> JobOutcome:
    common = {
        "job_id": job.job_id,
        "benchmark_id": result.benchmark_id,
        "planned": result.planned,
        "completed": result.completed,
        "remaining": result.remaining,
        "completed_by_band": result.completed_by_band,
        "newly_fetched": result.newly_fetched,
        "cache_hits": result.cache_hits,
    }

    if result.state is BenchmarkBuildState.READY:
        queue.mark_done(job.job_id, report_path=None)
        log.info("benchmark_job_ready", **common)
        message = f"benchmark {result.benchmark_id} pronto ({result.completed}/{result.planned})"
        return JobOutcome(job=job, ok=True, message=message)

    if result.state is BenchmarkBuildState.NO_PROGRESS:
        return _defer(
            queue,
            job,
            deps,
            reason="benchmark_no_progress",
            event="benchmark_job_no_progress",
            common=common,
        )

    if result.state is BenchmarkBuildState.DEFERRED_BUDGET:
        return _defer(
            queue,
            job,
            deps,
            reason="benchmark_deferred_budget",
            event="benchmark_job_deferred",
            common=common,
        )

    # BenchmarkBuildState.FAILED — hoje o builder nunca devolve isto (falhas
    # reais propagam como excecao, tratadas acima); mantido por completude do
    # mapeamento de estado, nunca "escondido" como sucesso.
    error = f"benchmark {result.benchmark_id} falhou: {result.failure_reason or 'sem detalhe'}"
    queue.mark_failed(job.job_id, error=error)
    log.error("benchmark_job_failed", reason="builder_failed_state", **common)
    return JobOutcome(job=job, ok=False, message=error)


def _retry_after_s(deps: Deps, reason: str) -> float:
    """Um adiamento por BUDGET real precisa esperar o orçamento melhorar —
    reusa `points_reset_in` quando disponível, senão o padrão conservador
    (`benchmark_build_defer_retry_s`). Um adiamento causado só pelo TETO de
    lotes por reivindicação (fairness, `advance_benchmark_build`'s
    `max_batches`) não tem por que esperar nada — o orçamento está bom, só
    cedeu a vez para o worker poder pegar trabalho quente antes de
    continuar. A distinção reusa `affordable_fights`, a MESMA função que o
    builder já chamou instantes atrás — nunca uma segunda estimativa de
    custo, só a mesma resposta relida.
    """
    if reason == "benchmark_no_progress":
        return deps.settings.benchmark_build_defer_retry_s

    available = deps.client.points_remaining
    if available is not None and affordable_fights(deps.settings, available, planned=1) > 0:
        return 0.0  # só o teto de fairness, não orçamento — retoma quase já
    retry_after = deps.client.points_reset_in
    if retry_after is None or retry_after <= 0:
        return deps.settings.benchmark_build_defer_retry_s
    return retry_after


def _defer(
    queue: JobQueue,
    job: Job,
    deps: Deps,
    *,
    reason: str,
    event: str,
    common: dict[str, object],
) -> JobOutcome:
    """DEFERRED_BUDGET (orçamento) e NO_PROGRESS (candidatos ainda
    retentáveis, sem trabalho novo NESTA janela) recebem o MESMO tratamento
    de fila — `JobQueue.defer()`, o mesmo primitivo que B2 já corrigiu para
    `deferred_budget` retomar sozinho via `Job.is_claimable()`, sem depender
    de outro job `queued` aparecer. A distinção entre as duas causas fica só
    no `reason`/evento de telemetria — o comportamento de retomada é idêntico
    de propósito, incluindo o teto de defers consecutivos.
    """
    if job.defer_count + 1 >= _MAX_CONSECUTIVE_DEFERS:
        error = (
            f"benchmark {common['benchmark_id']} excedeu {_MAX_CONSECUTIVE_DEFERS} adiamentos "
            f"consecutivos (última causa: {reason})"
        )
        queue.mark_failed(job.job_id, error=error)
        log.error("benchmark_job_failed", reason="defer_limit_exceeded", **common)
        return JobOutcome(job=job, ok=False, message=error)

    retry_after = _retry_after_s(deps, reason)
    until = queue.defer(job.job_id, retry_after_s=retry_after, reason=reason)
    log.info(event, retry_after_s=round(retry_after, 1), deferred_until=until.isoformat(), **common)
    return JobOutcome(
        job=job,
        ok=False,
        message=f"benchmark adiado ({reason}): {common['completed']}/{common['planned']}",
        deferred=True,
        deferred_until=until.isoformat(),
    )
