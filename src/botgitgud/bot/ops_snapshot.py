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

import json
import os
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


@dataclass(frozen=True, slots=True)
class OpsSnapshot:
    pid: int
    written_at: float
    queued: int
    running: int
    oldest_running_started_at: str | None
    points_remaining: float | None
    points_limit: float | None
    budget_checked_at: float | None
    age_seconds: float = 0.0

    @property
    def is_stale(self) -> bool:
        return self.age_seconds > STALE_AFTER_S


def write_snapshot(
    data_dir: Path,
    *,
    pid: int | None = None,
    active: Sequence[Job],
    points_remaining: float | None = None,
    points_limit: float | None = None,
    now: float | None = None,
) -> None:
    """Publica o estado atual. Escrita atômica (tmp + replace) para que um
    leitor nunca veja JSON pela metade.
    """
    moment = time.time() if now is None else now
    running = [j for j in active if j.status == "running"]
    oldest = min((j.started_at for j in running if j.started_at is not None), default=None)
    payload = {
        "pid": os.getpid() if pid is None else pid,
        "written_at": moment,
        "queued": sum(1 for j in active if j.status == "queued"),
        "running": len(running),
        "oldest_running_started_at": oldest.isoformat() if oldest is not None else None,
        "points_remaining": points_remaining,
        "points_limit": points_limit,
        "budget_checked_at": None if points_remaining is None else moment,
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
