"""Artefato auditável por análise (`data/ops/analysis-runs/<id>.json`).

O smoke do Fiskowl teve sucesso funcional e ainda assim foi classificado FAIL:
não havia como provar, depois do processo morrer, qual coorte foi usada, se
houve cold build, quantas queries WCL ocorreram ou se o relatório foi persistido
antes do envio. Tudo isso vivia em stdout.

Arquivo JSON ao lado do warehouse — não no DuckDB, para não reintroduzir a D-34
(observabilidade que exige derrubar o processo dono). Escrita atômica, como o
ops-snapshot e o report_store.
"""

from __future__ import annotations

import contextlib
import json
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from botgitgud.bot.ops_snapshot import _json_safe
from botgitgud.telemetry import AnalysisRecorder, QueryRole, recording

ANALYSIS_RUNS_DIRNAME = "ops/analysis-runs"

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class AnalysisRunPersistenceError(RuntimeError):
    """Falha ao gravar a telemetria. Nunca deve derrubar a análise em si."""


def new_analysis_id() -> str:
    return uuid.uuid4().hex


def analysis_runs_dir(data_dir: Path) -> Path:
    return data_dir / ANALYSIS_RUNS_DIRNAME


def analysis_run_path(data_dir: Path, analysis_id: str) -> Path:
    if not _SAFE_ID.match(analysis_id):
        raise AnalysisRunPersistenceError(f"analysis_id invalido: {analysis_id!r}")
    return analysis_runs_dir(data_dir) / f"{analysis_id}.json"


@dataclass
class AnalysisRun:
    """Campos ausentes ficam `None` — nunca um valor inventado."""

    analysis_id: str
    started_at: str
    finished_at: str | None = None

    # alvo
    player: str | None = None
    report_code: str | None = None
    fight_id: int | None = None
    class_name: str | None = None
    spec_name: str | None = None
    encounter_id: int | None = None
    difficulty: int | None = None
    partition: int | None = None
    duration_s: float | None = None

    # coorte
    cohort_id: str | None = None
    cohort_state: str | None = None
    # Compatibilidade: cohort_members conserva o significado historico de
    # membros pos-matching. Os nomes explicitos removem a ambiguidade.
    cohort_members: int | None = None
    reference_pool_members: int | None = None
    matched_cohort_members: int | None = None

    # caminho
    hot_path: bool | None = None
    cold_build_started: bool = False

    # cache de referências
    reference_members_expected: int | None = None
    reference_members_cache_hit: int | None = None
    reference_members_refetched: int | None = None

    # contabilidade WCL
    wcl_queries_total: int | None = None
    wcl_queries_by_op_name: dict[str, int] = field(default_factory=dict)
    queries_by_role: dict[str, dict[str, int]] = field(default_factory=dict)
    wcl_retries: int | None = None
    reference_query_count: int | None = None
    player_query_count: int | None = None

    wcl_points_before: float | None = None
    wcl_points_after: float | None = None
    wcl_points_consumed: float | None = None
    accounting_reason: str | None = None

    # relatório e entrega
    report_artifact_id: str | None = None
    report_path_exists: bool | None = None
    report_persisted_at: str | None = None
    delivery_started_at: str | None = None
    delivery_finished_at: str | None = None
    delivery_status: str | None = None
    channel_id: str | None = None
    guild_id: str | None = None

    # desfecho
    final_status: str = "running"
    error_type: str | None = None
    error_message: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


def compute_points_consumed(
    before: float | None, after: float | None
) -> tuple[float | None, str | None]:
    """`None` com motivo explícito quando o delta não é confiável.

    A janela horária da WCL reseta sozinha; se `after > before`, houve reset no
    meio da execução e a subtração produziria um consumo negativo/absurdo. Nesse
    caso o honesto é declarar que não é mensurável.
    """
    if before is None or after is None:
        return None, "budget_unknown"
    if after > before:
        return None, "window_reset"
    return before - after, None


def write_analysis_run(data_dir: Path, run: AnalysisRun) -> Path:
    """Escrita atômica (tmp + replace): um leitor nunca vê JSON parcial."""
    target = analysis_run_path(data_dir, run.analysis_id)
    tmp = target.with_suffix(".json.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(run.as_payload()), encoding="utf-8")
        tmp.replace(target)
    except OSError as e:
        raise AnalysisRunPersistenceError(f"falha ao gravar telemetria: {e}") from e
    return target


def read_analysis_run(data_dir: Path, analysis_id: str) -> dict[str, Any] | None:
    try:
        raw = analysis_run_path(data_dir, analysis_id).read_text(encoding="utf-8")
    except (OSError, AnalysisRunPersistenceError):
        return None
    try:
        payload: Any = json.loads(raw)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def summarize_for_snapshot(run: AnalysisRun) -> dict[str, Any]:
    """Resumo pequeno para o ops-snapshot — o payload detalhado fica no arquivo."""
    return {
        "last_analysis_id": run.analysis_id,
        "last_analysis_status": run.final_status,
        "last_analysis_queries": run.wcl_queries_total,
        "last_analysis_points": run.wcl_points_consumed,
        "last_analysis_delivery": run.delivery_status,
    }


@contextmanager
def track_analysis(
    data_dir: Path,
    *,
    budget: Any | None = None,
    player: str | None = None,
    report_code: str | None = None,
    fight_id: int | None = None,
) -> Iterator[AnalysisRun]:
    """Envolve uma análise: cria o recorder, publica o artefato ao sair.

    Sai pelo `finally`, então um `return` antecipado ou uma exceção ainda
    deixam telemetria auditável — o requisito é justamente não depender de
    sucesso total. Falhar ao gravar telemetria nunca derruba a análise.
    """
    run = AnalysisRun(
        analysis_id=new_analysis_id(),
        started_at=datetime.now(UTC).isoformat(),
        player=player,
        report_code=report_code,
        fight_id=fight_id,
        wcl_points_before=getattr(budget, "points_remaining", None),
    )
    recorder = AnalysisRecorder()
    try:
        with recording(recorder):
            yield run
    except BaseException as e:  # reclassifica e repropaga logo abaixo
        if run.final_status == "running":
            run.final_status = "analysis_failed"
            run.error_type = type(e).__name__
            run.error_message = str(e)
        raise
    finally:
        run.finished_at = datetime.now(UTC).isoformat()
        run.wcl_queries_total = recorder.queries_total
        run.wcl_queries_by_op_name = recorder.queries_by_op_name
        run.queries_by_role = recorder.queries_by_role
        run.wcl_retries = recorder.retries
        run.reference_query_count = recorder.queries_for(QueryRole.REFERENCE)
        run.player_query_count = recorder.queries_for(QueryRole.PLAYER_ANALYZED)
        run.reference_members_expected = recorder.reference_members_expected
        run.reference_members_cache_hit = recorder.reference_members_cache_hit
        run.reference_members_refetched = recorder.reference_members_refetched
        run.wcl_points_after = getattr(budget, "points_remaining", None)
        run.wcl_points_consumed, run.accounting_reason = compute_points_consumed(
            run.wcl_points_before, run.wcl_points_after
        )
        if run.final_status == "running":
            run.final_status = "completed"
        with contextlib.suppress(AnalysisRunPersistenceError):
            write_analysis_run(data_dir, run)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
