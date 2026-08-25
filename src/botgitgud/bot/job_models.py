"""T1.8 split of bot/jobs.py's data model out of JobQueue's logic, to keep
both files under the 300-line limit (docs/implementacao.md T1.6's rule,
still enforced repo-wide). See jobs.py's module docstring for context.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]
JobType = Literal["analyze", "build_cohort"]

# RC.2 — o estado da ANALISE e o estado da ENTREGA sao fatos diferentes. Uma
# falha do Discord nao pode reescrever "a analise terminou" como "o job
# falhou": o incidente real produziu exatamente esse buraco (analise done,
# relatorio perdido, usuario sem nada). Ver docs/rc-discord-delivery-
# resilience.md.
DeliveryStatus = Literal["pending", "delivered", "failed"]

INTERACTIVE_RESERVE_PCT = 0.25

# docs/desvios.md D-20: the literal `jobs` DDL in docs/implementacao.md has
# no column to distinguish an `analyze` job from a `build_cohort` job, but
# §5 explicitly requires "duas filas com prioridade" between them — added
# `job_type`.
CREATE_JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id VARCHAR PRIMARY KEY,
    job_type VARCHAR,
    dedup_key VARCHAR,
    discord_user_id VARCHAR, discord_channel_id VARCHAR,
    status VARCHAR,
    created_at TIMESTAMP, started_at TIMESTAMP, finished_at TIMESTAMP,
    error VARCHAR, report_path VARCHAR,
    delivery_status VARCHAR, delivery_error VARCHAR
)
"""

# CREATE TABLE IF NOT EXISTS nao adiciona coluna a uma tabela que ja existe —
# um warehouse anterior ao RC precisa destas colunas para nao quebrar.
MIGRATE_JOBS_TABLE = (
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS delivery_status VARCHAR",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS delivery_error VARCHAR",
)
# Plain TIMESTAMP, not TIMESTAMPTZ: DuckDB's TIMESTAMPTZ support needs the
# optional `pytz` package (not in this project's dependency list, §1.2 —
# "não adicione dependências fora desta lista sem registrar um desvio").
# A timezone-aware datetime.now(UTC) written here comes back offset-naive
# on read — every datetime this module writes/compares is therefore always
# UTC-but-naive (see now_utc_naive() below); never mix in a tz-aware value.

JOB_COLUMNS = (
    "job_id, job_type, dedup_key, discord_user_id, discord_channel_id, "
    "status, created_at, started_at, finished_at, error, report_path, "
    "delivery_status, delivery_error"
)


def now_utc_naive() -> datetime:
    """The one way this module should ever get "now" — see CREATE_JOBS_TABLE's
    docstring for why every stored/compared datetime here is naive-but-UTC.
    """
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass(frozen=True, slots=True)
class Job:
    job_id: str
    job_type: JobType
    dedup_key: str
    discord_user_id: str
    discord_channel_id: str
    status: JobStatus
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    report_path: str | None
    delivery_status: DeliveryStatus = "pending"
    delivery_error: str | None = None

    @property
    def analysis_completed(self) -> bool:
        """RC.2: verdadeiro mesmo quando a entrega falhou."""
        return self.status == "done"


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    job: Job | None  # None only when rejected
    deduped: bool
    queue_position: int | None = None
    rejected_reason: str | None = None


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    """T1.8 §3: the token-bucket policy for which job types may run right
    now, given the account's live rateLimitData.

    Note: with the account's real confirmed limitPerHour=3600
    (docs/schema_confirmado.md §2), the 25% interactive reserve (900) is
    actually *below* T0.3's own floor default (1000) — so with the
    literal default numbers, the floor always binds before the reserve
    ever would. The two thresholds still compose correctly in general
    (any account with a high enough limitPerHour that reserve > floor
    gets a real middle tier); see docs/desvios.md D-21.
    """

    points_remaining: float
    limit_per_hour: float
    floor: float = 1000.0
    interactive_reserve_pct: float = INTERACTIVE_RESERVE_PCT

    @property
    def reserve_threshold(self) -> float:
        return self.limit_per_hour * self.interactive_reserve_pct

    def allows(self, job_type: JobType) -> bool:
        if self.points_remaining < self.floor:
            return False
        return not (job_type == "build_cohort" and self.points_remaining < self.reserve_threshold)


def row_to_job(row: tuple[object, ...]) -> Job:
    return Job(
        job_id=row[0],  # type: ignore[arg-type]
        job_type=row[1],  # type: ignore[arg-type]
        dedup_key=row[2],  # type: ignore[arg-type]
        discord_user_id=row[3],  # type: ignore[arg-type]
        discord_channel_id=row[4],  # type: ignore[arg-type]
        status=row[5],  # type: ignore[arg-type]
        created_at=row[6],  # type: ignore[arg-type]
        started_at=row[7],  # type: ignore[arg-type]
        finished_at=row[8],  # type: ignore[arg-type]
        error=row[9],  # type: ignore[arg-type]
        report_path=row[10],  # type: ignore[arg-type]
        delivery_status=row[11] or "pending",  # type: ignore[arg-type]
        delivery_error=row[12],  # type: ignore[arg-type]
    )
