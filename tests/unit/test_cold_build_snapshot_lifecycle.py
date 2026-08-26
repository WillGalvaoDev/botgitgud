"""Bug de observabilidade encontrado no PRIMEIRO PREWARM REAL.

Durante toda a construcao real (37 refs, 864 queries, 3m30s) o `ops-status`
mostrou `stage=preflight`. O snapshot final ficou correto por acidente: o CLI
sobrescrevia o valor estatico no fim.

Causa: `cold.update(lifecycle)` seguido de `cold.update(self._extra)` — o
contexto ESTATICO do chamador era aplicado DEPOIS do lifecycle vivo e o
mascarava. A regra estrutural agora e a inversa: contexto estatico nunca define
estado dinamico, e `set_context` recusa esses campos por construcao.

Nada aqui toca WCL ou Discord.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from botgitgud.analysis.cold_build import record_cold_lifecycle
from botgitgud.bot.ops_snapshot import (
    LIFECYCLE_OWNED_FIELDS,
    SNAPSHOT_FILENAME,
    ColdBuildPublisher,
    read_snapshot,
)
from botgitgud.cli_ops import warehouse_status

COHORT_ID = "f91dffaf13ddc899"


class _Budget:
    points_remaining = 3599.0
    points_limit = 3600.0


@pytest.fixture(autouse=True)
def _reset_lifecycle() -> None:
    """O lifecycle e um global do processo; cada teste parte do zero."""
    import botgitgud.analysis.cold_build as cold_build_module

    cold_build_module._cold_lifecycle = None


def _stage(tmp_path: Path) -> str | None:
    snapshot = read_snapshot(tmp_path)
    assert snapshot is not None
    assert snapshot.cold_build is not None
    return snapshot.cold_build.get("stage")


# -- 4: o teste de regressao mais direto do bug real ---------------------------------


def test_static_context_can_never_define_a_lifecycle_field() -> None:
    """Falharia contra 176341a, onde `stage` estatico era aceito e vencia."""
    publisher = ColdBuildPublisher(Path(), interval_s=60.0)
    with pytest.raises(ValueError, match="lifecycle"):
        publisher.set_context(stage="preflight")


@pytest.mark.parametrize("field", sorted(LIFECYCLE_OWNED_FIELDS))
def test_every_lifecycle_field_is_refused_as_static_context(field: str) -> None:
    publisher = ColdBuildPublisher(Path(), interval_s=60.0)
    with pytest.raises(ValueError):
        publisher.set_context(**{field: "x"})


def test_live_lifecycle_wins_over_static_context(tmp_path: Path) -> None:
    """A regressao direta: contexto estatico presente + lifecycle em building
    -> o snapshot precisa mostrar building.
    """
    with ColdBuildPublisher(tmp_path, budget=_Budget(), interval_s=60.0) as pub:
        pub.set_context(requested_bucket=493.312)
        record_cold_lifecycle("building", COHORT_ID, planned=37)
        pub.publish()
        assert _stage(tmp_path) == "building"
        snapshot = read_snapshot(tmp_path)
        assert snapshot is not None and snapshot.cold_build is not None
        # o contexto estatico continua visivel, so nao manda no estado
        assert snapshot.cold_build["requested_bucket"] == 493.312
        assert snapshot.cold_build["cohort_id"] == COHORT_ID


# -- 1/2/3: sequencias de lifecycle observaveis ---------------------------------------


def _observed_sequence(tmp_path: Path, stages: list[str]) -> list[str | None]:
    seen: list[str | None] = []
    with ColdBuildPublisher(tmp_path, budget=_Budget(), interval_s=60.0) as pub:
        pub.set_context(requested_bucket=493.312)
        for stage in stages:
            record_cold_lifecycle(stage, COHORT_ID, planned=37)
            pub.publish()
            seen.append(_stage(tmp_path))
    seen.append(_stage(tmp_path))  # depois do __exit__
    return seen


def test_normal_prewarm_shows_preflight_then_building_then_completed(tmp_path: Path) -> None:
    seen = _observed_sequence(tmp_path, ["preflight", "building", "completed"])
    assert seen == ["preflight", "building", "completed", "completed"]


def test_deferred_prewarm_shows_preflight_then_building_then_deferred(tmp_path: Path) -> None:
    seen = _observed_sequence(tmp_path, ["preflight", "building", "deferred_budget"])
    assert seen == ["preflight", "building", "deferred_budget", "deferred_budget"]


def test_failed_prewarm_shows_preflight_then_building_then_failed(tmp_path: Path) -> None:
    seen = _observed_sequence(tmp_path, ["preflight", "building", "failed"])
    assert seen == ["preflight", "building", "failed", "failed"]


def test_stage_never_stays_pinned_at_preflight_while_building(tmp_path: Path) -> None:
    """O sintoma exato observado em producao."""
    with ColdBuildPublisher(tmp_path, budget=_Budget(), interval_s=60.0) as pub:
        pub.set_context(requested_bucket=493.312)
        record_cold_lifecycle("preflight", COHORT_ID)
        pub.publish()
        assert _stage(tmp_path) == "preflight"
        record_cold_lifecycle("building", COHORT_ID, planned=37)
        for _ in range(3):  # varias amostragens, como o thread faria
            pub.publish()
            assert _stage(tmp_path) == "building"


# -- 5: estado final persiste ---------------------------------------------------------


@pytest.mark.parametrize("terminal", ["completed", "deferred_budget", "failed"])
def test_terminal_stage_survives_the_publisher(tmp_path: Path, terminal: str) -> None:
    with ColdBuildPublisher(tmp_path, budget=_Budget(), interval_s=60.0) as pub:
        pub.set_context(requested_bucket=493.312)
        pub.record(terminal, COHORT_ID, outcome=terminal)
    assert _stage(tmp_path) == terminal


def test_record_publishes_through_the_same_channel_as_the_builder(tmp_path: Path) -> None:
    with ColdBuildPublisher(tmp_path, budget=_Budget(), interval_s=60.0) as pub:
        pub.record("building", COHORT_ID, planned=37, completed=12)
        snapshot = read_snapshot(tmp_path)
    assert snapshot is not None and snapshot.cold_build is not None
    assert snapshot.cold_build["stage"] == "building"
    assert snapshot.cold_build["planned"] == 37
    assert snapshot.cold_build["completed"] == 12


# -- 6/7: freshness e atomicidade preservadas ------------------------------------------


def test_snapshot_is_fresh_during_building(tmp_path: Path) -> None:
    with ColdBuildPublisher(tmp_path, budget=_Budget(), interval_s=60.0) as pub:
        pub.record("building", COHORT_ID, planned=37)
        snapshot = read_snapshot(tmp_path)
        assert snapshot is not None
        assert snapshot.is_stale is False


def test_write_stays_atomic_with_no_partial_file(tmp_path: Path) -> None:
    with ColdBuildPublisher(tmp_path, budget=_Budget(), interval_s=60.0) as pub:
        for i in range(6):
            pub.record("building", COHORT_ID, planned=37, completed=i)
    assert sorted(p.name for p in tmp_path.iterdir()) == [SNAPSHOT_FILENAME]
    json.loads((tmp_path / SNAPSHOT_FILENAME).read_text(encoding="utf-8"))


def test_budget_fields_survive_the_precedence_change(tmp_path: Path) -> None:
    with ColdBuildPublisher(tmp_path, budget=_Budget(), interval_s=60.0) as pub:
        pub.record("building", COHORT_ID, planned=37)
        snapshot = read_snapshot(tmp_path)
    assert snapshot is not None
    assert snapshot.points_remaining == 3599.0
    assert snapshot.points_limit == 3600.0
    assert snapshot.cold_build is not None
    assert snapshot.cold_build["mode"] == "prewarm"


# -- 8: ops-status observa building sem abrir o DuckDB ----------------------------------


def test_ops_status_sees_building_without_opening_the_warehouse(tmp_path: Path) -> None:
    with ColdBuildPublisher(tmp_path, budget=_Budget(), interval_s=60.0) as pub:
        pub.set_context(requested_bucket=493.312)
        pub.record("building", COHORT_ID, planned=37)

        assert not (tmp_path / "warehouse.duckdb").exists()
        lines = warehouse_status(tmp_path)
        assert lines is not None
        text = "\n".join(lines)
        assert "'stage': 'building'" in text
        assert COHORT_ID in text
        assert "prewarm" in text


# -- B1: o build frio INTERATIVO tambem precisa ser observavel ----------------------


def test_interactive_cold_build_lifecycle_is_visible_without_reading_duckdb(
    tmp_path: Path,
) -> None:
    """D-34 vale para o caminho interativo também: enquanto o `serve` roda,
    ninguém consegue abrir o warehouse de fora. Se o adiamento e o progresso do
    build só existissem no banco, o operador não teria como ver por que uma
    análise não anda.
    """
    record_cold_lifecycle(
        "deferred_budget",
        COHORT_ID,
        mode="interactive",
        state="deferred_budget",
        job_id="job-42",
        planned=100,
        completed=30,
        remaining=70,
        reason="budget",
        current_budget=1200.0,
        estimated_points_remaining=3608.5,
    )
    publisher = ColdBuildPublisher(tmp_path, budget=_Budget(), interval_s=60.0)
    publisher.publish()

    snapshot = read_snapshot(tmp_path)
    assert snapshot is not None
    cold = snapshot.cold_build
    assert cold is not None
    assert cold["mode"] == "interactive"
    assert cold["state"] == "deferred_budget"
    assert cold["cohort_id"] == COHORT_ID
    assert cold["job_id"] == "job-42"
    assert (cold["planned"], cold["completed"], cold["remaining"]) == (100, 30, 70)
    assert cold["estimated_points_remaining"] == pytest.approx(3608.5)
    assert cold["current_budget"] == pytest.approx(1200.0)
    assert json.loads((tmp_path / SNAPSHOT_FILENAME).read_text(encoding="utf-8"))


def test_every_new_lifecycle_field_is_owned_by_the_lifecycle_not_the_caller() -> None:
    """Campos dinâmicos novos herdam a mesma regra estrutural do bug do
    prewarm: contexto estático nunca define estado dinâmico.
    """
    for field in ("state", "job_id", "batch_size", "current_budget"):
        assert field in LIFECYCLE_OWNED_FIELDS
