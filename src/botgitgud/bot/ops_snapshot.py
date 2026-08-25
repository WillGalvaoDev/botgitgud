"""R3-01 (reaberta) — estado operacional publicado pelo processo `serve`.

O smoke A de R1-01 provou que enquanto `serve` roda ele segura o arquivo do
DuckDB, e o DuckDB 1.5.5 nega até `read_only=True` a partir de outro processo
(docs/desvios.md D-34). Logo `ops-status` não tem como ler a fila enquanto o
bot está no ar — exatamente quando o operador mais precisa dela.

A solução mínima é o próprio bot, que já é o dono da conexão, publicar um
resumo pequeno num arquivo JSON ao lado do warehouse. Nada de daemon, socket,
porta HTTP ou segundo banco: um arquivo escrito de forma atômica a cada tick
do worker loop, e lido por `ops-status` quando o banco está travado.

O snapshot carrega apenas contadores e timestamps. Nunca credencial, nunca
nome de jogador, nunca conteúdo de relatório. O orçamento aparece apenas se o
bot já o tiver consultado por conta própria — este módulo jamais dispara uma
chamada à WCL para preencher o campo.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from botgitgud.bot.job_models import Job

SNAPSHOT_FILENAME = "ops-snapshot.json"

# Acima disto o snapshot descreve um bot que parou de escrever. O worker loop
# escreve a cada _WORKER_POLL_INTERVAL_S (2s), então 30s são ~15 ticks perdidos.
STALE_AFTER_S = 30.0

# Campos cujo dono e o lifecycle da operacao. Contexto estatico do chamador
# nunca pode defini-los — ver ColdBuildPublisher.set_context.
LIFECYCLE_OWNED_FIELDS = frozenset(
    {
        "stage",
        "outcome",
        "cohort_id",
        "planned",
        "completed",
        "remaining",
        "n_members",
        "buckets",
        "reason",
        "timestamp",
    }
)


@dataclass(frozen=True, slots=True)
class OpsSnapshot:
    schema_version: int
    pid: int
    written_at: float
    queued: int
    running: int
    oldest_running_started_at: str | None
    points_remaining: float | None
    points_limit: float | None
    budget_checked_at: float | None
    worker_alive: bool
    done: int
    failed: int
    active_job: dict[str, Any] | None
    latest_completed_job: dict[str, Any] | None
    ready_cohorts: list[dict[str, Any]]
    cold_build: dict[str, Any] | None
    age_seconds: float = 0.0

    @property
    def is_stale(self) -> bool:
        return self.age_seconds > STALE_AFTER_S


def write_snapshot(
    data_dir: Path,
    *,
    pid: int | None = None,
    active: Sequence[Job],
    recent: Sequence[Job] = (),
    ready_cohorts: Sequence[dict[str, Any]] = (),
    worker_alive: bool = True,
    cold_build: dict[str, Any] | None = None,
    points_remaining: float | None = None,
    points_limit: float | None = None,
    now: float | None = None,
) -> None:
    """Publica o estado atual. Escrita atômica (tmp + replace) para que um
    leitor nunca veja JSON pela metade.
    """
    moment = time.time() if now is None else now
    running = [j for j in active if j.status == "running"]
    completed = [j for j in recent if j.status in {"done", "failed"}]
    latest = max(completed, key=lambda j: j.finished_at or j.created_at, default=None)
    active_job = running[0] if running else None

    def job_summary(job: Job | None) -> dict[str, Any] | None:
        if job is None:
            return None
        return {
            "job_id": job.job_id,
            "job_type": job.job_type,
            "status": job.status,
            "delivery_status": job.delivery_status,
            "report_path_exists": bool(job.report_path and Path(job.report_path).is_file()),
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        }

    oldest = min((j.started_at for j in running if j.started_at is not None), default=None)
    payload = {
        "schema_version": 2,
        "pid": os.getpid() if pid is None else pid,
        "written_at": moment,
        "queued": sum(1 for j in active if j.status == "queued"),
        "running": len(running),
        "oldest_running_started_at": oldest.isoformat() if oldest is not None else None,
        "points_remaining": points_remaining,
        "points_limit": points_limit,
        "budget_checked_at": None if points_remaining is None else moment,
        "worker_alive": worker_alive,
        "done": sum(1 for j in recent if j.status == "done"),
        "failed": sum(1 for j in recent if j.status == "failed"),
        "active_job": job_summary(active_job),
        "latest_completed_job": job_summary(latest),
        "ready_cohorts": list(ready_cohorts),
        "cold_build": cold_build,
    }
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / SNAPSHOT_FILENAME
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(target)


def read_snapshot(data_dir: Path, *, now: float | None = None) -> OpsSnapshot | None:
    """Devolve o snapshot publicado, ou None se ausente/ilegível — um arquivo
    corrompido nunca deve derrubar um comando de diagnóstico.
    """
    target = data_dir / SNAPSHOT_FILENAME
    try:
        payload: Any = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    known = {f.name for f in fields(OpsSnapshot)} - {"age_seconds"}
    if not known <= set(payload):
        return None
    moment = time.time() if now is None else now
    try:
        return OpsSnapshot(
            **{k: payload[k] for k in known},
            age_seconds=moment - float(payload["written_at"]),
        )
    except (TypeError, ValueError):
        return None


class ColdBuildPublisher:
    """D-34 aplicado ao prewarm: durante `build-cohort` o processo dono do
    DuckDB e o proprio CLI, entao e ele quem precisa publicar o snapshot.

    Reusa `write_snapshot` (mesma escrita atomica); nao ha segunda
    implementacao. Um thread daemon reamostra o lifecycle enquanto o build
    corre, e o estado final e escrito de forma sincrona na saida — para que
    `ops-status` explique o ultimo desfecho mesmo com o processo ja encerrado.
    """

    def __init__(
        self,
        data_dir: Path,
        *,
        budget: Any | None = None,
        interval_s: float = 1.0,
    ) -> None:
        self._data_dir = data_dir
        self._budget = budget
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._context: dict[str, Any] = {}

    def set_context(self, **fields: Any) -> None:
        """Contexto ESTATICO da execucao: o que o comando sabe de antemao e nao
        muda (modo, bucket solicitado, parametros).

        Recusa campos de lifecycle por construcao. No prewarm real, um
        `stage="preflight"` estatico mascarou o `stage="building"` vivo durante
        toda a construcao — o observador via `preflight` do inicio ao fim. A
        regra estrutural agora e: **contexto estatico nunca define estado
        dinamico**.
        """
        invalid = LIFECYCLE_OWNED_FIELDS & set(fields)
        if invalid:
            raise ValueError(
                f"campos de lifecycle nao podem vir do contexto estatico: {sorted(invalid)}. "
                "Use record() para estado dinamico."
            )
        self._context.update(fields)
        self.publish()

    def record(self, stage: str, cohort_id: str = "", **details: Any) -> None:
        """Estado DINAMICO, pelo mesmo canal que o construtor usa. Assim a
        ordem e cronologica por natureza e nao ha duas fontes competindo.
        """
        from botgitgud.analysis.cold_build import record_cold_lifecycle

        record_cold_lifecycle(stage, cohort_id, **details)
        self.publish()

    def publish(self) -> None:
        from botgitgud.analysis.cold_build import cold_lifecycle_snapshot

        # Ordem de precedencia: modo -> contexto estatico -> lifecycle vivo.
        # O lifecycle e SEMPRE o ultimo, logo sempre autoritativo.
        cold: dict[str, Any] = {"mode": "prewarm"}
        cold.update(self._context)
        lifecycle = cold_lifecycle_snapshot()
        if lifecycle is not None:
            cold.update(lifecycle)
        points_remaining = getattr(self._budget, "points_remaining", None)
        points_limit = getattr(self._budget, "points_limit", None)
        # Publicar diagnostico nunca pode derrubar o prewarm.
        with contextlib.suppress(OSError):
            write_snapshot(
                self._data_dir,
                active=[],
                worker_alive=False,
                cold_build=cold,
                points_remaining=points_remaining,
                points_limit=points_limit,
            )

    def __enter__(self) -> ColdBuildPublisher:
        self.publish()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval_s * 2)
        self.publish()  # estado final, sincrono

    def _loop(self) -> None:
        while not self._stop.wait(self._interval_s):
            self.publish()
