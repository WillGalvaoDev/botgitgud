"""Local operational diagnostics and crash recovery for the v1.0 runbook.

R3-01 reaberta após o smoke A de R1-01 (docs/desvios.md D-34): enquanto
`serve` roda, ele segura o arquivo do DuckDB e nenhuma outra conexão entra —
nem `read_only=True`. Por isso `ops-status` tem dois modos:

- **bot parado**: lê o warehouse direto e reporta tudo.
- **bot rodando**: lê o snapshot que o próprio bot publica
  (`bot/ops_snapshot.py`) e diz explicitamente que a origem é o processo vivo.

Nenhum dos dois consulta a WCL. Um lock nunca produz traceback: é condição
operacional esperada, tratada com mensagem acionável e exit code próprio.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

from botgitgud.bot.jobs import JobQueue
from botgitgud.bot.ops_snapshot import SNAPSHOT_FILENAME, read_snapshot
from botgitgud.ingest.store import Store

EX_TEMPFAIL = 75  # BSD sysexits.h — mesma convenção de build-cohort/experiment-collect

# duckdb sinaliza "arquivo em uso" de formas diferentes conforme o chamador
# esteja em outro processo (IOException) ou no mesmo (ConnectionException).
_IN_USE_ERRORS = (duckdb.IOException, duckdb.ConnectionException)

_LOCKED_HINT = (
    "erro: warehouse em uso por outro processo (o bot provavelmente está rodando) "
    f"e nenhum {SNAPSHOT_FILENAME} legível foi encontrado.\n"
    f"  - com o bot no ar, o estado vem do ops-snapshot publicado por ele; "
    "aguarde alguns segundos e repita.\n"
    "  - para inspeção completa do warehouse, pare o bot e rode de novo.\n"
)


def _snapshot_lines(data_dir: Path) -> list[str] | None:
    snapshot = read_snapshot(data_dir)
    if snapshot is None:
        return None
    lines = [
        "warehouse=locked_by_running_bot",
        "source=running_bot",
        f"bot_pid={snapshot.pid}",
        f"snapshot_age_s={snapshot.age_seconds:.1f}",
        f"snapshot_stale={str(snapshot.is_stale).lower()}",
        f"schema_version={snapshot.schema_version}",
        f"worker_alive={str(snapshot.worker_alive).lower()}",
        f"jobs queued={snapshot.queued} running={snapshot.running}",
        f"jobs done={snapshot.done} failed={snapshot.failed}",
        f"oldest_running_started_at={snapshot.oldest_running_started_at}",
    ]
    for job_type in sorted(snapshot.by_type):
        by_status = snapshot.by_type[job_type]
        counts = " ".join(f"{status}={n}" for status, n in sorted(by_status.items()))
        lines.append(f"jobs[{job_type}] {counts}")
    if snapshot.active_job is not None:
        lines.append("active_job=" + str(snapshot.active_job))
    if snapshot.latest_completed_job is not None:
        lines.append("latest_completed_job=" + str(snapshot.latest_completed_job))
    lines.append(f"cohorts ready={len(snapshot.ready_cohorts)} known={len(snapshot.ready_cohorts)}")
    for cohort in snapshot.ready_cohorts:
        lines.append("cohort=" + str(cohort))
    lines.append(
        "cold_build=" + ("none" if snapshot.cold_build is None else str(snapshot.cold_build))
    )
    if snapshot.points_remaining is None:
        lines.append("points_remaining=not_queried_yet limit=not_queried_yet")
    else:
        limit = "unknown" if snapshot.points_limit is None else f"{snapshot.points_limit:.0f}"
        lines.append(f"points_remaining={snapshot.points_remaining:.0f} limit={limit}")
    if snapshot.is_stale:
        lines.append("warning=snapshot_stale_bot_may_have_stopped_writing")
    return lines


def _local_lines(db_path: Path, data_dir: Path) -> list[str]:
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        tables = {str(row[0]) for row in conn.execute("SHOW TABLES").fetchall()}
        lines = [
            "warehouse=ok",
            "source=local_warehouse",
            f"warehouse_bytes={db_path.stat().st_size}",
            f"raw_parquet_files={sum(1 for _ in (data_dir / 'raw').rglob('*.parquet'))}",
        ]
        if "jobs" not in tables:
            lines.append("jobs_table=missing queued=0 running=0 failed=0 done=0")
        else:
            counts = dict(
                conn.execute("SELECT status, count(*) FROM jobs GROUP BY status").fetchall()
            )
            lines.append(
                "jobs "
                + " ".join(
                    f"{status}={int(counts.get(status, 0))}"
                    for status in ("queued", "running", "failed", "done")
                )
            )
        if "cohort_registry" in tables:
            cohorts = conn.execute(
                """SELECT cohort_id, class_name, spec_name, encounter_id, difficulty,
                          partition, n_members, updated_at
                   FROM cohort_registry ORDER BY updated_at DESC"""
            ).fetchall()
            lines.append(f"cohorts ready={len(cohorts)} known={len(cohorts)}")
            lines.extend(f"cohort={row}" for row in cohorts)
        else:
            lines.append("cohorts ready=0 known=0")
        lines.append("tables=" + ",".join(sorted(tables)))
        return lines
    finally:
        conn.close()


def warehouse_status(data_dir: Path) -> list[str] | None:
    """Inspect the warehouse read-only; never creates tables or calls an API.

    Devolve None quando o warehouse está travado e não há snapshot legível —
    o chamador transforma isso em erro controlado.
    """
    snapshot = read_snapshot(data_dir)
    if snapshot is not None and not snapshot.is_stale:
        return _snapshot_lines(data_dir)
    db_path = data_dir / "warehouse.duckdb"
    if not db_path.is_file():
        return [f"warehouse=missing path={db_path}"]
    try:
        return _local_lines(db_path, data_dir)
    except _IN_USE_ERRORS:
        return _snapshot_lines(data_dir)


def _cmd_ops_status(args: argparse.Namespace) -> int:
    lines = warehouse_status(args.data_dir)
    if lines is None:
        sys.stderr.write(_LOCKED_HINT)
        return EX_TEMPFAIL
    for line in lines:
        sys.stdout.write(line + "\n")
    sys.stdout.write(f"api_points_floor={args.api_points_floor:.0f} live_budget=not_queried\n")
    return 0


def _cmd_recover_jobs(args: argparse.Namespace) -> int:
    try:
        with Store(args.data_dir) as store:
            recovered = JobQueue(store).recover_from_crash()
    except _IN_USE_ERRORS:
        sys.stderr.write(
            "erro: warehouse em uso por outro processo. Recuperar jobs exige escrita, "
            "então pare o bot antes de rodar este comando.\n"
            "  - o próprio boot do bot já reverte jobs presos em running "
            "(on_ready chama recover_from_crash).\n"
        )
        return EX_TEMPFAIL
    sys.stdout.write(f"recovered_jobs={recovered}\n")
    return 0


def add_ops_parsers(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],  # type: ignore[name-defined]
) -> None:
    status = sub.add_parser("ops-status", help="Saúde local read-only do warehouse e da fila.")
    status.add_argument("--data-dir", type=Path, default=Path("data"))
    status.add_argument("--api-points-floor", type=float, default=1000.0)
    status.set_defaults(func=_cmd_ops_status)

    recover = sub.add_parser(
        "recover-jobs", help="Move jobs presos em running de volta para queued (exige bot parado)."
    )
    recover.add_argument("--data-dir", type=Path, default=Path("data"))
    recover.set_defaults(func=_cmd_recover_jobs)
