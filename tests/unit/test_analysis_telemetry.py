"""HOT-PATH smoke do Fiskowl: sucesso funcional, FAIL operacional.

A analise usou a coorte certa e entregou o relatorio, mas depois que o processo
morreu nao havia como provar QUAL coorte, se houve cold build, quantas queries
WCL ocorreram, nem se o relatorio foi persistido antes do envio — tudo vivia em
stdout.

Estes testes exercem o artefato de telemetria que torna uma analise auditavel
sem depender do terminal da execucao original. Zero rede.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from botgitgud.bot.analysis_runs import (
    AnalysisRun,
    AnalysisRunPersistenceError,
    analysis_run_path,
    analysis_runs_dir,
    compute_points_consumed,
    new_analysis_id,
    read_analysis_run,
    summarize_for_snapshot,
    track_analysis,
    write_analysis_run,
)
from botgitgud.bot.discord_bot import record_analysis_result, run_in_executor_with_context
from botgitgud.telemetry import (
    AnalysisRecorder,
    QueryRole,
    record_query,
    record_reference_batch,
    recording,
    role_scope,
)

COHORT_ID = "f91dffaf13ddc899"


class _Budget:
    def __init__(self, *values: float | None) -> None:
        self._values = list(values)

    @property
    def points_remaining(self) -> float | None:
        return self._values.pop(0) if len(self._values) > 1 else self._values[0]


# -- artefato ---------------------------------------------------------------------


def test_interactive_analysis_creates_a_telemetry_artifact(tmp_path: Path) -> None:
    with track_analysis(tmp_path, player="Fiskowl", report_code="FhYZDLMbwBVAx4KX", fight_id=19):
        pass
    files = list(analysis_runs_dir(tmp_path).glob("*.json"))
    assert len(files) == 1


def test_artifact_is_json_safe_and_survives_the_process(tmp_path: Path) -> None:
    with track_analysis(tmp_path, player="Fiskowl") as run:
        run.cohort_id = COHORT_ID
        analysis_id = run.analysis_id
    payload = read_analysis_run(tmp_path, analysis_id)  # leitura independente
    assert payload is not None
    assert payload["cohort_id"] == COHORT_ID
    json.dumps(payload)  # ja e JSON puro


def test_write_is_atomic_and_leaves_no_partial_file(tmp_path: Path) -> None:
    run = AnalysisRun(analysis_id=new_analysis_id(), started_at="2026-08-26T00:00:00+00:00")
    for _ in range(4):
        write_analysis_run(tmp_path, run)
    names = sorted(p.name for p in analysis_runs_dir(tmp_path).iterdir())
    assert names == [f"{run.analysis_id}.json"]


def test_analysis_id_can_never_escape_the_directory(tmp_path: Path) -> None:
    with pytest.raises(AnalysisRunPersistenceError):
        analysis_run_path(tmp_path, "../../etc/passwd")


def test_started_and_finished_timestamps_are_recorded(tmp_path: Path) -> None:
    with track_analysis(tmp_path) as run:
        analysis_id = run.analysis_id
    payload = read_analysis_run(tmp_path, analysis_id)
    assert payload is not None
    started = datetime.fromisoformat(payload["started_at"])
    finished = datetime.fromisoformat(payload["finished_at"])
    assert finished >= started


# -- coorte e caminho ---------------------------------------------------------------


def test_cohort_and_hot_path_are_persisted(tmp_path: Path) -> None:
    with track_analysis(tmp_path) as run:
        run.cohort_id = COHORT_ID
        run.cohort_state = "ready"
        run.cohort_members = 37
        run.hot_path = True
        analysis_id = run.analysis_id
    payload = read_analysis_run(tmp_path, analysis_id)
    assert payload is not None
    assert payload["cohort_id"] == COHORT_ID
    assert payload["cohort_state"] == "ready"
    assert payload["cohort_members"] == 37
    assert payload["hot_path"] is True


def test_cold_build_started_defaults_to_false_on_the_hot_path(tmp_path: Path) -> None:
    with track_analysis(tmp_path) as run:
        run.hot_path = True
        analysis_id = run.analysis_id
    payload = read_analysis_run(tmp_path, analysis_id)
    assert payload is not None
    assert payload["cold_build_started"] is False


# -- cache de referencias ------------------------------------------------------------


def test_fiskowl_like_cache_telemetry_is_recorded(tmp_path: Path) -> None:
    """O caso ideal do prewarm: 37 esperados, 37 em cache, 0 refetched."""
    with track_analysis(tmp_path) as run:
        record_reference_batch(expected=37, cache_hits=37, fetched=0)
        analysis_id = run.analysis_id
    payload = read_analysis_run(tmp_path, analysis_id)
    assert payload is not None
    assert payload["reference_members_expected"] == 37
    assert payload["reference_members_cache_hit"] == 37
    assert payload["reference_members_refetched"] == 0


def test_a_refetch_is_visible_in_the_artifact(tmp_path: Path) -> None:
    with track_analysis(tmp_path) as run:
        record_reference_batch(expected=37, cache_hits=30, fetched=7)
        analysis_id = run.analysis_id
    payload = read_analysis_run(tmp_path, analysis_id)
    assert payload is not None
    assert payload["reference_members_refetched"] == 7


def test_partial_cache_records_37_35_2_including_non_falsy_zero_rules(tmp_path: Path) -> None:
    with track_analysis(tmp_path) as run:
        record_reference_batch(expected=37, cache_hits=35, fetched=2)
        analysis_id = run.analysis_id
    payload = read_analysis_run(tmp_path, analysis_id)
    assert payload is not None
    assert (
        payload["reference_members_expected"],
        payload["reference_members_cache_hit"],
        payload["reference_members_refetched"],
    ) == (37, 35, 2)


def test_ready_pool_and_matched_sample_are_distinct_without_changing_legacy_field() -> None:
    run = AnalysisRun(analysis_id="fiskowl", started_at="2026-08-26T00:00:00+00:00")
    result = SimpleNamespace(
        header=SimpleNamespace(
            class_name="Warlock", spec="Demonology", duration_max_s=515.0, reference_n=8
        ),
        reference_pool_members=37,
        matched_cohort_members=8,
        encounter_id=3183,
        difficulty=5,
        manifest=SimpleNamespace(cohort_id=COHORT_ID, wcl_partition=3),
    )
    record_analysis_result(run, result)
    assert run.reference_pool_members == 37
    assert run.matched_cohort_members == 8
    assert run.cohort_members == 8
    assert (run.encounter_id, run.difficulty) == (3183, 5)


# -- contabilidade de queries ---------------------------------------------------------


def test_player_and_reference_queries_are_counted_separately(tmp_path: Path) -> None:
    with track_analysis(tmp_path) as run:
        with role_scope(QueryRole.PLAYER_ANALYZED):
            record_query("fetch_player_meta")
            record_query("fetch_player_damage_events")
            record_query("fetch_player_damage_events")
        with role_scope(QueryRole.REFERENCE):
            record_query("fetch_player_meta")
        analysis_id = run.analysis_id
    payload = read_analysis_run(tmp_path, analysis_id)
    assert payload is not None
    assert payload["player_query_count"] == 3
    assert payload["reference_query_count"] == 1
    assert payload["wcl_queries_total"] == 4


def test_hot_path_can_prove_zero_reference_queries(tmp_path: Path) -> None:
    """O invariante central do hot path apos prewarm."""
    with track_analysis(tmp_path) as run:
        with role_scope(QueryRole.PLAYER_ANALYZED):
            record_query("fetch_player_meta")
        record_reference_batch(expected=37, cache_hits=37, fetched=0)
        analysis_id = run.analysis_id
    payload = read_analysis_run(tmp_path, analysis_id)
    assert payload is not None
    assert payload["reference_query_count"] == 0
    assert payload["reference_members_refetched"] == 0


def test_query_breakdown_by_op_name(tmp_path: Path) -> None:
    with track_analysis(tmp_path) as run:
        record_query("fetch_player_meta")
        record_query("fetch_player_damage_events")
        record_query("fetch_player_damage_events")
        analysis_id = run.analysis_id
    payload = read_analysis_run(tmp_path, analysis_id)
    assert payload is not None
    assert payload["wcl_queries_by_op_name"] == {
        "fetch_player_damage_events": 2,
        "fetch_player_meta": 1,
    }


def test_query_breakdown_by_role_and_operation_has_one_authoritative_total(tmp_path: Path) -> None:
    with track_analysis(tmp_path) as run:
        with role_scope(QueryRole.PLAYER_ANALYZED):
            record_query("fetch_player_meta")
            record_query("fetch_player_damage_events")
        with role_scope(QueryRole.REFERENCE):
            record_query("fetch_player_damage_events")
            record_query("fetch_player_damage_events")
        analysis_id = run.analysis_id
    payload = read_analysis_run(tmp_path, analysis_id)
    assert payload is not None
    assert payload["queries_by_role"]["player_analyzed"] == {
        "fetch_player_damage_events": 1,
        "fetch_player_meta": 1,
    }
    assert payload["queries_by_role"]["reference"] == {"fetch_player_damage_events": 2}
    assert payload["queries_by_role"]["reference"].get("fetch_report_rankings", 0) == 0
    assert payload["player_query_count"] == 2
    assert payload["reference_query_count"] == 2
    assert payload["wcl_queries_total"] == 4


def test_zero_query_hot_path_persists_all_query_zeros_not_null(tmp_path: Path) -> None:
    with track_analysis(tmp_path) as run:
        record_reference_batch(expected=37, cache_hits=37, fetched=0)
        analysis_id = run.analysis_id
    payload = read_analysis_run(tmp_path, analysis_id)
    assert payload is not None
    assert payload["wcl_queries_total"] == 0
    assert payload["player_query_count"] == 0
    assert payload["reference_query_count"] == 0
    assert payload["wcl_retries"] == 0
    assert payload["queries_by_role"]["reference"] == {}
    assert payload["reference_members_refetched"] == 0


def test_retries_are_counted_from_the_attempt_number(tmp_path: Path) -> None:
    with track_analysis(tmp_path) as run:
        record_query("fetch_report_rankings", attempt=1)
        record_query("fetch_report_rankings", attempt=2)  # retry
        record_query("fetch_report_rankings", attempt=3)  # retry
        analysis_id = run.analysis_id
    payload = read_analysis_run(tmp_path, analysis_id)
    assert payload is not None
    assert payload["wcl_retries"] == 2
    assert payload["wcl_queries_total"] == 3


def test_role_is_not_inferred_from_op_name() -> None:
    """A mesma op_name em papeis diferentes conta em cada um."""
    recorder = AnalysisRecorder()
    with recording(recorder):
        with role_scope(QueryRole.PLAYER_ANALYZED):
            record_query("fetch_player_meta")
        with role_scope(QueryRole.REFERENCE):
            record_query("fetch_player_meta")
    assert recorder.queries_for(QueryRole.PLAYER_ANALYZED) == 1
    assert recorder.queries_for(QueryRole.REFERENCE) == 1


def test_analysis_recorder_crosses_the_interactive_executor_boundary() -> None:
    recorder = AnalysisRecorder()

    async def exercise() -> None:
        loop = asyncio.get_running_loop()

        def pipeline_thread() -> None:
            with role_scope(QueryRole.PLAYER_ANALYZED):
                record_query("fetch_player_meta")

        with recording(recorder):
            await run_in_executor_with_context(loop, pipeline_thread)

    asyncio.run(exercise())
    assert recorder.queries_by_role["player_analyzed"] == {"fetch_player_meta": 1}


def test_recording_outside_an_analysis_is_a_noop() -> None:
    record_query("fetch_player_meta")  # sem recorder ativo: nao levanta
    record_reference_batch(expected=1, cache_hits=1, fetched=0)


# -- orcamento -------------------------------------------------------------------------


def test_points_before_after_and_consumed(tmp_path: Path) -> None:
    with track_analysis(tmp_path, budget=_Budget(3599.0, 3585.0)) as run:
        analysis_id = run.analysis_id
    payload = read_analysis_run(tmp_path, analysis_id)
    assert payload is not None
    assert payload["wcl_points_before"] == 3599.0
    assert payload["wcl_points_after"] == 3585.0
    assert payload["wcl_points_consumed"] == 14.0
    assert payload["accounting_reason"] is None


def test_window_reset_never_fabricates_a_consumption() -> None:
    consumed, reason = compute_points_consumed(500.0, 3600.0)
    assert consumed is None
    assert reason == "window_reset"


def test_unknown_budget_is_null_with_a_reason() -> None:
    assert compute_points_consumed(None, 100.0) == (None, "budget_unknown")
    assert compute_points_consumed(100.0, None) == (None, "budget_unknown")


# -- entrega ---------------------------------------------------------------------------


def test_persist_before_send_is_provable_from_the_artifact(tmp_path: Path) -> None:
    with track_analysis(tmp_path) as run:
        run.report_persisted_at = "2026-08-26T00:00:01+00:00"
        run.delivery_started_at = "2026-08-26T00:00:02+00:00"
        run.delivery_finished_at = "2026-08-26T00:00:03+00:00"
        run.delivery_status = "delivered"
        analysis_id = run.analysis_id
    payload = read_analysis_run(tmp_path, analysis_id)
    assert payload is not None
    persisted = datetime.fromisoformat(payload["report_persisted_at"])
    started = datetime.fromisoformat(payload["delivery_started_at"])
    assert persisted < started
    assert payload["delivery_status"] == "delivered"


def test_delivery_failure_is_persisted(tmp_path: Path) -> None:
    with track_analysis(tmp_path) as run:
        run.report_persisted_at = "2026-08-26T00:00:01+00:00"
        run.delivery_status = "failed"
        run.channel_id = "1529559571968954489"
        run.guild_id = "204649688400527360"
        analysis_id = run.analysis_id
    payload = read_analysis_run(tmp_path, analysis_id)
    assert payload is not None
    assert payload["delivery_status"] == "failed"
    assert payload["channel_id"] == "1529559571968954489"
    assert payload["guild_id"] == "204649688400527360"


# -- falhas ------------------------------------------------------------------------------


def test_analysis_failure_is_persisted_and_the_error_propagates(tmp_path: Path) -> None:
    analysis_id: str | None = None
    with pytest.raises(RuntimeError, match="pipeline quebrou"), track_analysis(tmp_path) as run:
        analysis_id = run.analysis_id
        raise RuntimeError("pipeline quebrou")
    assert analysis_id is not None
    payload = read_analysis_run(tmp_path, analysis_id)
    assert payload is not None
    assert payload["final_status"] == "analysis_failed"
    assert payload["error_type"] == "RuntimeError"
    assert "pipeline quebrou" in payload["error_message"]


def test_report_persistence_failure_is_persisted(tmp_path: Path) -> None:
    with track_analysis(tmp_path) as run:
        run.final_status = "report_persistence_failed"
        run.report_path_exists = False
        analysis_id = run.analysis_id
    payload = read_analysis_run(tmp_path, analysis_id)
    assert payload is not None
    assert payload["final_status"] == "report_persistence_failed"
    assert payload["report_path_exists"] is False


def test_an_early_return_still_leaves_telemetry(tmp_path: Path) -> None:
    def handler() -> str:
        with track_analysis(tmp_path) as run:
            run.final_status = "insufficient_cohort"
            return run.analysis_id

    analysis_id = handler()
    payload = read_analysis_run(tmp_path, analysis_id)
    assert payload is not None
    assert payload["final_status"] == "insufficient_cohort"


def test_telemetry_write_failure_never_breaks_the_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import botgitgud.bot.analysis_runs as runs_module

    def boom(*_a: object, **_k: object) -> None:
        raise AnalysisRunPersistenceError("disco cheio")

    monkeypatch.setattr(runs_module, "write_analysis_run", boom)
    with track_analysis(tmp_path) as run:  # nao levanta
        run.hot_path = True


# -- ops-status ----------------------------------------------------------------------------


def test_snapshot_summary_is_small_and_references_the_run() -> None:
    run = AnalysisRun(
        analysis_id="abc123",
        started_at="2026-08-26T00:00:00+00:00",
        final_status="completed",
        wcl_queries_total=4,
        wcl_points_consumed=6.0,
        delivery_status="delivered",
    )
    summary: dict[str, Any] = summarize_for_snapshot(run)
    assert summary["last_analysis_id"] == "abc123"
    assert summary["last_analysis_status"] == "completed"
    assert summary["last_analysis_queries"] == 4
    assert summary["last_analysis_delivery"] == "delivered"
    assert len(summary) <= 6  # resumo, nao payload
