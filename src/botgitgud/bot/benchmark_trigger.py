"""EB.6 — o único gatilho de produção do `benchmark_build`.

EB.5 já tinha `ensure_benchmark_job` (enfileira, dedup por `benchmark_id`,
respeita freshness, prioridade mais baixa da fila) mas NENHUM call site de
produção: nada em `src/` a chamava, então nenhum `benchmark_build` podia
nascer num bot real e `BenchmarkStore.read_benchmark` devolvia `None` para
sempre — Setup Analysis ficava permanentemente no escuro. Este módulo é
exatamente esse call site, e nada além dele.

**Por que aqui e não em `analysis/pipeline.py`.** `Deps` (pipeline) não tem
`JobQueue`, e não pode ter: `bot/` importa `analysis/`, então uma fila
dentro de `analysis/` inverteria a direção da dependência. O gatilho vive
na camada que JÁ tem a fila em mãos (`bot/discord_bot.py` no caminho
interativo, `bot/worker.py` no caminho da fila), e recebe do
`AnalysisResult` a identidade que o pipeline já resolveu
(`benchmark_target`/`benchmark_policy`) — nunca reconstruída aqui, para
nunca divergir da identidade que `read_benchmark` consultou.

**Custo.** Zero WCL. Zero discovery. Zero rankings. Zero setup fetch. Este
módulo só lê tabelas locais (`benchmark_build_progress`,
`encounter_benchmarks`, `jobs`) e reusa o candidate pool que a própria
análise acabou de usar. O trabalho caro é do job de EB.4/EB.5, em
background, no orçamento dele.

**Isolamento de falha.** Benchmark é enriquecimento: a análise já terminou
e o relatório já foi (ou vai ser) entregue quando isto roda. Qualquer falha
aqui é registrada com evento estruturado próprio e engolida — nunca
transforma um `analyze` bem-sucedido em falha. Engolida não é silenciosa:
`benchmark_trigger.failed` sai em nível `error`, com a exceção.
"""

from __future__ import annotations

from collections.abc import Sequence

import structlog

from botgitgud.analysis.benchmark import EncounterBenchmarkTarget
from botgitgud.analysis.benchmark_build_progress import BenchmarkBuildProgressStore
from botgitgud.analysis.pipeline import AnalysisResult, Deps
from botgitgud.bot.benchmark_job import EnsureBenchmarkJobResult, ensure_benchmark_job
from botgitgud.bot.jobs import JobQueue
from botgitgud.domain.models import RankingCandidate

log = structlog.get_logger(__name__)


def resolve_current_population_size(
    progress_store: BenchmarkBuildProgressStore,
    *,
    target: EncounterBenchmarkTarget,
    candidates: Sequence[RankingCandidate],
) -> int:
    """O "tamanho da população atual" NO MESMO universo lógico que
    `encounter_benchmarks.source_observation_count` — o outro lado da
    comparação que `evaluate_benchmark_freshness` (EB.3) faz.

    Alinhar os dois universos é obrigatório, e não é cosmético:

    * O lado PERSISTIDO é `EncounterBenchmark.total_input_observations`
      (EB.2), que `benchmark_builder._finalize` (EB.4) alimenta APENAS com
      as linhas de progresso `fetched` — um candidato que falhou
      permanentemente (`MAX_CANDIDATE_ATTEMPTS`, EB.4) nunca entra e nunca
      vai entrar.
    * Passar `len(candidates)` cru como lado ATUAL contaria justamente
      esses candidatos inalcançáveis. Um pool de 40 com 10 falhas
      permanentes viraria `(40-30)/30 = 0.33 > 0.30` -> `population_growth`
      -> stale -> enfileira -> o job reencontra zero pendentes, reescreve o
      MESMO benchmark, e a próxima análise repete tudo: stale permanente,
      um job inútil por análise. Descontar as falhas permanentes não é um
      workaround do sintoma; é medir a mesma coisa dos dois lados —
      "observações utilizáveis para este benchmark".

    A união com as linhas já registradas (em vez de só `candidates`) é a
    outra metade do alinhamento: `_finalize` agrega TODAS as linhas
    `fetched` do `benchmark_id`, acumuladas por todos os pools que já
    dispararam um build (o pool é por `cohort_id`, que inclui o bucket de
    duração; o benchmark não). Sem a união, um pool de OUTRO bucket, do
    mesmo tamanho mas disjunto, mediria "crescimento zero" trazendo 100% de
    população nova. Com ela, cada pool novo é absorvido uma vez e o sistema
    converge para `fresh` — nunca um loop.
    """
    rows = progress_store.read_progress(target.benchmark_id)
    unusable = {r.candidate_key for r in rows if r.status == "failed"}
    known = {r.candidate_key for r in rows if r.status != "failed"}
    for c in candidates:
        key = (c.report_code, c.fight_id, c.player_name)
        if key not in unusable:
            known.add(key)
    return len(known)


def maybe_enqueue_benchmark_build(
    *,
    deps: Deps,
    queue: JobQueue,
    result: AnalysisResult,
    source: str,
) -> EnsureBenchmarkJobResult | None:
    """Chamado DEPOIS de uma análise bem-sucedida, nos dois caminhos reais
    (`source="discord_direct"` / `source="worker"`). Devolve `None` quando
    não havia o que decidir (resultado sem identidade de benchmark, pool
    ausente) ou quando o gatilho falhou — nunca levanta.

    Não decide sozinho se o benchmark está fresco/stale/ausente: isso é
    `ensure_benchmark_job` (EB.5), que já faz freshness (EB.3) + dedup
    atômico por `dedup_key` (single-flight por `benchmark_id`) + prioridade
    mais baixa. EB.6 não duplica nada disso, e em particular NÃO tem um
    modelo de custo próprio.
    """
    target = result.benchmark_target
    policy = result.benchmark_policy
    if target is None or policy is None:
        log.debug("benchmark_trigger.skipped", reason="no_benchmark_identity", source=source)
        return None

    try:
        # O MESMO pool que esta análise acabou de consumir — o pipeline o
        # leu (ou o escreveu, no cold build) sob este `cohort_id`, então
        # isto é releitura local, nunca uma nova descoberta na WCL.
        candidates = deps.store.read_candidate_pool(result.manifest.cohort_id)
        if not candidates:
            log.info(
                "benchmark_trigger.skipped",
                reason="empty_candidate_pool",
                source=source,
                benchmark_id=target.benchmark_id,
                cohort_id=result.manifest.cohort_id,
            )
            return None

        progress_store = BenchmarkBuildProgressStore(deps.store)
        population = resolve_current_population_size(
            progress_store, target=target, candidates=candidates
        )
        outcome = ensure_benchmark_job(
            deps=deps,
            queue=queue,
            target=target,
            policy=policy,
            candidates=candidates,
            current_population_size=population,
        )
    except Exception as e:  # enriquecimento nunca derruba a análise já concluída
        log.error(
            "benchmark_trigger.failed",
            source=source,
            benchmark_id=target.benchmark_id,
            error=str(e),
            error_type=type(e).__name__,
            exc_info=True,
        )
        return None

    log.info(
        "benchmark_trigger.evaluated",
        source=source,
        benchmark_id=target.benchmark_id,
        cohort_id=result.manifest.cohort_id,
        reason=outcome.reason,
        created=outcome.created,
        job_id=None if outcome.job is None else outcome.job.job_id,
        candidates=len(candidates),
        current_population_size=population,
    )
    return outcome
