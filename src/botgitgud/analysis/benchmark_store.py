"""EB.3 — persistência do Encounter Benchmark: uma tabela dedicada no mesmo
warehouse DuckDB que `ingest/store.py`'s `Store` já possui (nunca um banco
separado, nunca JSON solto em disco — o projeto já centraliza estado
persistente no `Store`), seguindo o mesmo padrão de extensão que
`bot/jobs.py`'s `JobQueue` já usa: uma classe própria que recebe um `Store`
e escreve na sua tabela via os métodos SQL genéricos de `Store`
(`execute`/`execute_returning`), então toda escrita do processo continua
serializada por `Store`'s único lock (docs/architecture.md D-19).

Nenhuma construção automática de benchmark aqui — `write_benchmark` recebe
um `EncounterBenchmark` já pronto (de `analysis/benchmark_aggregate.py`).
Nenhuma integração com jobs/pipeline/`!analisar` — isso é EB.4+.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget
from botgitgud.analysis.benchmark_aggregate import EncounterBenchmark
from botgitgud.analysis.benchmark_store_models import (
    CREATE_ENCOUNTER_BENCHMARKS_TABLE,
    DEFAULT_FRESHNESS_POLICY,
    MIGRATE_ENCOUNTER_BENCHMARKS_TABLE,
    BenchmarkFreshness,
    BenchmarkFreshnessPolicy,
    StalenessReason,
    decode_benchmark,
    encode_benchmark,
    now_utc_naive,
    policy_fingerprint,
    population_fingerprint,
    population_growth_ratio,
)
from botgitgud.domain.models import PlayerLog
from botgitgud.ingest.store import Store

_LIST_COLUMNS = (
    "benchmark_id",
    "class_name",
    "spec_name",
    "encounter_id",
    "difficulty",
    "partition",
    "benchmark_policy_version",
    "created_at",
    "updated_at",
    "source_observation_count",
    "eligible_observation_count",
    "setup_available_count",
    "setup_missing_count",
    "deduped_count",
    "population_fingerprint",
    "policy_fingerprint",
)


class BenchmarkStore:
    def __init__(self, store: Store) -> None:
        self._store = store
        self._store.execute(CREATE_ENCOUNTER_BENCHMARKS_TABLE)
        for statement in MIGRATE_ENCOUNTER_BENCHMARKS_TABLE:
            self._store.execute(statement)

    # -- write ---------------------------------------------------------------------

    def write_benchmark(
        self,
        benchmark: EncounterBenchmark,
        *,
        policy: BenchmarkPolicy,
        observations: Sequence[PlayerLog],
    ) -> None:
        """UPSERT por `benchmark_id` (a identidade completa de EB.1 — spec/
        encounter/difficulty/partition/policy_version): a MESMA identidade
        atualiza a linha existente; qualquer dimensão diferente (partition
        OU policy_version) já produz um `benchmark_id` diferente, então
        coexiste automaticamente como uma linha nova, sem lógica extra.

        Atômico por construção: um único `INSERT ... ON CONFLICT DO UPDATE`
        é uma única transação implícita do DuckDB — nunca há uma linha
        parcialmente atualizada (ou o statement inteiro aplica, ou nenhuma
        mudança acontece). `created_at` fica FORA do `SET` do conflito, então
        um upsert nunca reescreve a data de criação original — só
        `updated_at` e o resto avançam.
        """
        if policy.policy_version != benchmark.policy_version:
            raise ValueError(
                f"policy.policy_version ({policy.policy_version!r}) não corresponde a "
                f"benchmark.policy_version ({benchmark.policy_version!r}) — o caller "
                "passou uma policy diferente da que gerou este benchmark"
            )

        target = benchmark.target
        now = now_utc_naive()
        self._store.execute(
            """
            INSERT INTO encounter_benchmarks (
                benchmark_id, class_name, spec_name, encounter_id, difficulty, partition,
                benchmark_policy_version, created_at, updated_at,
                source_observation_count, eligible_observation_count,
                setup_available_count, setup_missing_count, deduped_count,
                population_fingerprint, policy_fingerprint, benchmark_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (benchmark_id) DO UPDATE SET
                updated_at = EXCLUDED.updated_at,
                source_observation_count = EXCLUDED.source_observation_count,
                eligible_observation_count = EXCLUDED.eligible_observation_count,
                setup_available_count = EXCLUDED.setup_available_count,
                setup_missing_count = EXCLUDED.setup_missing_count,
                deduped_count = EXCLUDED.deduped_count,
                population_fingerprint = EXCLUDED.population_fingerprint,
                policy_fingerprint = EXCLUDED.policy_fingerprint,
                benchmark_payload = EXCLUDED.benchmark_payload
            """,
            [
                target.benchmark_id,
                target.spec.class_name,
                target.spec.spec_name,
                target.encounter_id,
                target.difficulty,
                target.partition,
                target.benchmark_policy_version,
                now,
                now,
                benchmark.total_input_observations,
                benchmark.eligible_observations,
                benchmark.coverage.setup_available,
                benchmark.coverage.setup_missing,
                benchmark.deduped_count,
                population_fingerprint(observations),
                policy_fingerprint(policy),
                encode_benchmark(benchmark),
            ],
        )

    # -- read ----------------------------------------------------------------------

    def read_benchmark(self, benchmark_id: str) -> EncounterBenchmark | None:
        """`None` == nenhum benchmark com esta identidade EXATA foi
        persistido — nunca confundir com um payload corrompido, que
        levanta `EncounterBenchmarkCorruptPayloadError` em vez de retornar
        algo parcial.
        """
        rows = self._store.execute_returning(
            "SELECT benchmark_payload FROM encounter_benchmarks WHERE benchmark_id = ?",
            [benchmark_id],
        )
        if not rows:
            return None
        return decode_benchmark(rows[0][0])

    def list_benchmarks(self) -> list[dict[str, object]]:
        """Metadados de auditoria (sem o payload completo) — mesma forma de
        `Store.list_ready_cohorts`.
        """
        rows = self._store.execute_returning(
            f"SELECT {', '.join(_LIST_COLUMNS)} FROM encounter_benchmarks ORDER BY updated_at DESC"
        )
        return [dict(zip(_LIST_COLUMNS, row, strict=True)) for row in rows]

    # -- freshness -------------------------------------------------------------------

    def evaluate_benchmark_freshness(
        self,
        *,
        target: EncounterBenchmarkTarget,
        policy: BenchmarkPolicy,
        current_population_size: int,
        freshness_policy: BenchmarkFreshnessPolicy = DEFAULT_FRESHNESS_POLICY,
        now: datetime | None = None,
    ) -> BenchmarkFreshness:
        """Pergunta "o benchmark mais recente para esta spec/encontro/
        dificuldade ainda serve para o MEU target/policy atuais?" — não
        "existe um benchmark com este benchmark_id exato?" (isso é
        `read_benchmark`). É por isso que a busca aqui é pela identidade
        MAIS GROSSA (sem partition/policy_version), pegando a linha mais
        recente por `updated_at`: só assim uma mudança de partition ou de
        policy_version vira uma razão de staleness DETECTADA, em vez de
        simplesmente "missing" — que seria verdade mas menos informativo.
        """
        rows = self._store.execute_returning(
            """
            SELECT benchmark_id, partition, benchmark_policy_version, policy_fingerprint,
                   source_observation_count, updated_at
            FROM encounter_benchmarks
            WHERE class_name = ? AND spec_name = ? AND encounter_id = ? AND difficulty = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            [target.spec.class_name, target.spec.spec_name, target.encounter_id, target.difficulty],
        )
        if not rows:
            return BenchmarkFreshness(
                status="stale",
                reasons=("missing",),
                benchmark_id=None,
                age_days=None,
                population_growth_ratio=None,
            )

        (
            row_benchmark_id,
            row_partition,
            row_policy_version,
            row_policy_fingerprint,
            row_source_count,
            row_updated_at,
        ) = rows[0]

        try:
            reasons: list[StalenessReason] = []
            if row_partition != target.partition:
                reasons.append("partition_changed")
            if (
                row_policy_version != target.benchmark_policy_version
                or row_policy_fingerprint != policy_fingerprint(policy)
            ):
                reasons.append("policy_changed")

            age_days = ((now or now_utc_naive()) - row_updated_at).total_seconds() / 86400.0
            if age_days > freshness_policy.ttl_days:
                reasons.append("max_age_exceeded")

            growth_ratio = population_growth_ratio(row_source_count, current_population_size)
            if growth_ratio > freshness_policy.stale_population_growth_ratio:
                reasons.append("population_growth")

            return BenchmarkFreshness(
                status="stale" if reasons else "fresh",
                reasons=tuple(reasons),
                benchmark_id=row_benchmark_id,
                age_days=age_days,
                population_growth_ratio=growth_ratio,
            )
        except (TypeError, ValueError, AttributeError):
            return BenchmarkFreshness(
                status="stale",
                reasons=("corrupt",),
                benchmark_id=row_benchmark_id,
                age_days=None,
                population_growth_ratio=None,
            )
