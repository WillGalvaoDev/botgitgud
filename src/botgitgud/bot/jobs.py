"""T1.8 — persistent, multi-user job queue: dedup, per-user fairness,
priority scheduling, and a budget-aware claim step, all backed by a
`jobs` table in the same DuckDB warehouse Store already owns (so every
write in the process serializes through Store's one lock — see
ingest/store.py's module docstring and docs/desvios.md D-19). Data model
in bot/job_models.py (T1.8 split, see that module's docstring).
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta

from botgitgud.bot.job_models import (
    ACTIVE_STATUSES,
    CREATE_JOBS_TABLE,
    JOB_COLUMNS,
    MIGRATE_JOBS_TABLE,
    BudgetStatus,
    EnqueueResult,
    Job,
    JobStatus,
    JobType,
    now_utc_naive,
    row_to_job,
)
from botgitgud.ingest.store import Store

_ACTIVE_SQL = ", ".join(f"'{status}'" for status in ACTIVE_STATUSES)

MAX_ACTIVE_JOBS_PER_USER = 1
MAX_QUEUED_JOBS_PER_USER = 3
USER_COOLDOWN_S = 60.0
MAX_CONCURRENT_JOBS = 2


class JobQueue:
    """Every method that reads-then-writes (enqueue, claim_next) takes
    `self._lock` for the whole critical section — Store's own lock only
    serializes individual SQL statements, not a multi-statement decision
    (e.g. "count queued jobs, then maybe insert") against a concurrent
    caller doing the same thing.
    """

    def __init__(self, store: Store) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._store.execute(CREATE_JOBS_TABLE)
        for statement in MIGRATE_JOBS_TABLE:
            self._store.execute(statement)

    # -- enqueue / dedup / fairness -----------------------------------------------

    def enqueue(
        self,
        *,
        job_type: JobType,
        dedup_key: str,
        discord_user_id: str,
        discord_channel_id: str,
    ) -> EnqueueResult:
        with self._lock:
            existing = self._find_active_by_dedup_key(dedup_key)
            if existing is not None:
                return EnqueueResult(job=existing, deduped=True)

            last_created = self._last_created_at_for_user(discord_user_id)
            if last_created is not None:
                elapsed = (now_utc_naive() - last_created).total_seconds()
                if elapsed < USER_COOLDOWN_S:
                    wait_s = USER_COOLDOWN_S - elapsed
                    return EnqueueResult(
                        job=None,
                        deduped=False,
                        rejected_reason=f"aguarde mais {wait_s:.0f}s antes de pedir outra análise",
                    )

            active_count = self._count_active_for_user(discord_user_id)
            if active_count >= MAX_ACTIVE_JOBS_PER_USER + MAX_QUEUED_JOBS_PER_USER:
                return EnqueueResult(
                    job=None,
                    deduped=False,
                    rejected_reason=(
                        f"você já tem {active_count} análises ativas/na fila "
                        f"(máximo {MAX_ACTIVE_JOBS_PER_USER + MAX_QUEUED_JOBS_PER_USER})"
                    ),
                )

            job = Job(
                job_id=uuid.uuid4().hex,
                job_type=job_type,
                dedup_key=dedup_key,
                discord_user_id=discord_user_id,
                discord_channel_id=discord_channel_id,
                status="queued",
                created_at=now_utc_naive(),
                started_at=None,
                finished_at=None,
                error=None,
                report_path=None,
            )
            placeholders = ", ".join("?" * len(JOB_COLUMNS.split(", ")))
            self._store.execute(
                f"INSERT INTO jobs ({JOB_COLUMNS}) VALUES ({placeholders})",
                [
                    job.job_id,
                    job.job_type,
                    job.dedup_key,
                    job.discord_user_id,
                    job.discord_channel_id,
                    job.status,
                    job.created_at,
                    job.started_at,
                    job.finished_at,
                    job.error,
                    job.report_path,
                    job.delivery_status,
                    job.delivery_error,
                    job.deferred_until,
                    job.defer_reason,
                    job.defer_count,
                ],
            )
            position = self._count_by_status("queued")  # this job included: it's last-in-line
            return EnqueueResult(job=job, deduped=False, queue_position=position)

    # -- claiming / lifecycle -------------------------------------------------------

    def claim_next(self, budget: BudgetStatus) -> Job | None:
        """Never claims more than MAX_CONCURRENT_JOBS running at once;
        prefers `analyze` over `build_cohort` (§5); skips job types the
        budget doesn't currently allow (§3) — those stay queued, not
        cancelled.
        """
        with self._lock:
            if self._count_by_status("running") >= MAX_CONCURRENT_JOBS:
                return None

            allowed_types = [t for t in ("analyze", "build_cohort") if budget.allows(t)]  # type: ignore[list-item]
            if not allowed_types:
                return None

            placeholders = ", ".join("?" for _ in allowed_types)
            # Um job adiado por orcamento volta a ser elegivel sozinho, sem job
            # novo e sem perder a dedup_key — mas so depois de `deferred_until`.
            # Sem essa clausula o worker giraria em loop apertado sobre um
            # orcamento que so melhora no reset da janela.
            rows = self._store.execute_returning(
                f"""
                SELECT {JOB_COLUMNS} FROM jobs
                WHERE job_type IN ({placeholders})
                  AND (
                        status = 'queued'
                     OR (status = 'deferred_budget'
                         AND (deferred_until IS NULL OR deferred_until <= ?))
                  )
                ORDER BY CASE job_type WHEN 'analyze' THEN 0 ELSE 1 END, created_at ASC
                LIMIT 1
                """,
                [*allowed_types, now_utc_naive()],
            )
            if not rows:
                return None

            job = row_to_job(rows[0])
            started_at = now_utc_naive()
            self._store.execute(
                "UPDATE jobs SET status = 'running', started_at = ? WHERE job_id = ?",
                [started_at, job.job_id],
            )
            # `dedup_key` e `defer_count` sobrevivem a retomada: e o MESMO job,
            # nao um novo com o mesmo pedido.
            return Job(
                job_id=job.job_id,
                job_type=job.job_type,
                dedup_key=job.dedup_key,
                discord_user_id=job.discord_user_id,
                discord_channel_id=job.discord_channel_id,
                status="running",
                created_at=job.created_at,
                started_at=started_at,
                finished_at=None,
                error=None,
                report_path=None,
                deferred_until=None,
                defer_reason=job.defer_reason,
                defer_count=job.defer_count,
            )

    def mark_done(self, job_id: str, *, report_path: str | None = None) -> None:
        self._store.execute(
            "UPDATE jobs SET status = 'done', finished_at = ?, report_path = ? WHERE job_id = ?",
            [now_utc_naive(), report_path, job_id],
        )

    def mark_delivered(self, job_id: str) -> None:
        """RC.2: entrega confirmada. Nunca toca no estado da analise."""
        self._store.execute(
            "UPDATE jobs SET delivery_status = 'delivered', delivery_error = NULL WHERE job_id = ?",
            [job_id],
        )

    def mark_delivery_failed(self, job_id: str, *, error: str) -> None:
        """RC.2: a entrega falhou e a analise continua valida — `status` e
        `report_path` ficam intactos para o reenvio (RC.10).
        """
        self._store.execute(
            "UPDATE jobs SET delivery_status = 'failed', delivery_error = ? WHERE job_id = ?",
            [error, job_id],
        )

    def mark_failed(self, job_id: str, *, error: str) -> None:
        self._store.execute(
            "UPDATE jobs SET status = 'failed', finished_at = ?, error = ? WHERE job_id = ?",
            [now_utc_naive(), error, job_id],
        )

    def defer(self, job_id: str, *, retry_after_s: float, reason: str) -> datetime:
        """B2 — adiamento por orcamento: trabalho VALIDO aguardando budget.

        Nao e `mark_failed`: `finished_at` continua vazio, a `dedup_key` fica
        intacta, o progresso da coorte permanece no cache de logs e o job volta
        a ser elegivel sozinho depois de `deferred_until`. Devolve o instante
        agendado para que o chamador possa registra-lo na telemetria.
        """
        until = now_utc_naive() + timedelta(seconds=max(retry_after_s, 0.0))
        self._store.execute(
            "UPDATE jobs SET status = 'deferred_budget', started_at = NULL, "
            "deferred_until = ?, defer_reason = ?, "
            "defer_count = coalesce(defer_count, 0) + 1 WHERE job_id = ?",
            [until, reason, job_id],
        )
        return until

    def requeue(self, job_id: str) -> None:
        """Budget ran out mid-job (T1.8 §3: re-enqueue for after
        pointsResetIn) — back to `queued`, `started_at` cleared.
        """
        self._store.execute(
            "UPDATE jobs SET status = 'queued', started_at = NULL WHERE job_id = ?", [job_id]
        )

    def recover_from_crash(self) -> int:
        """Boot-time recovery (§8): any job stuck in `running` means the
        process died mid-job. Returns the number reverted.
        """
        rows = self._store.execute_returning("SELECT job_id FROM jobs WHERE status = 'running'")
        self._store.execute(
            "UPDATE jobs SET status = 'queued', started_at = NULL WHERE status = 'running'"
        )
        return len(rows)

    # -- lookups ----------------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        rows = self._store.execute_returning(
            f"SELECT {JOB_COLUMNS} FROM jobs WHERE job_id = ?", [job_id]
        )
        return row_to_job(rows[0]) if rows else None

    def list_active(self) -> list[Job]:
        rows = self._store.execute_returning(
            f"SELECT {JOB_COLUMNS} FROM jobs WHERE status IN ({_ACTIVE_SQL}) "
            "ORDER BY CASE status WHEN 'running' THEN 0 "
            "WHEN 'queued' THEN 1 ELSE 2 END, created_at ASC"
        )
        return [row_to_job(r) for r in rows]

    def list_recent(self, limit: int = 100) -> list[Job]:
        rows = self._store.execute_returning(
            f"SELECT {JOB_COLUMNS} FROM jobs ORDER BY created_at DESC LIMIT ?", [limit]
        )
        return [row_to_job(r) for r in rows]

    # -- internal (already lock-held by callers where it matters) ----------------

    def _find_active_by_dedup_key(self, dedup_key: str) -> Job | None:
        rows = self._store.execute_returning(
            f"SELECT {JOB_COLUMNS} FROM jobs WHERE dedup_key = ? "
            f"AND status IN ({_ACTIVE_SQL}) ORDER BY created_at DESC LIMIT 1",
            [dedup_key],
        )
        return row_to_job(rows[0]) if rows else None

    def _last_created_at_for_user(self, discord_user_id: str) -> datetime | None:
        rows = self._store.execute_returning(
            "SELECT created_at FROM jobs WHERE discord_user_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            [discord_user_id],
        )
        return rows[0][0] if rows else None  # type: ignore[return-value]

    def _count_active_for_user(self, discord_user_id: str) -> int:
        rows = self._store.execute_returning(
            f"SELECT count(*) FROM jobs WHERE discord_user_id = ? AND status IN ({_ACTIVE_SQL})",
            [discord_user_id],
        )
        return int(rows[0][0])

    def _count_by_status(self, status: JobStatus) -> int:
        rows = self._store.execute_returning("SELECT count(*) FROM jobs WHERE status = ?", [status])
        return int(rows[0][0])
