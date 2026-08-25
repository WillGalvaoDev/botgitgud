"""Safety gate pre-prewarm: o CLI representava DEFERRED_BUDGET como sucesso e o
processo batch nao publicava ops-snapshot.

Bug 1: `build-cohort` seguia pelo retorno normal com um bucket adiado — exit 0 e
mensagem "coorte(s) construida(s)" para trabalho incompleto.
Bug 2: durante o prewarm o CLI e o dono do DuckDB, mas nao publicava snapshot,
entao `ops-status` ficava com o estado obsoleto do bot (contradiz D-34).

Nada aqui toca WCL ou Discord.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import botgitgud.cli as cli_module
from botgitgud.analysis.cohort_builder import BucketBuildResult, CohortState
from botgitgud.bot.ops_snapshot import SNAPSHOT_FILENAME, ColdBuildPublisher, read_snapshot
from botgitgud.cli import EX_TEMPFAIL, build_cohort_exit_code
from botgitgud.cli_ops import warehouse_status
from botgitgud.errors import CohortDeferredBudget


@pytest.fixture(autouse=True)
def _reset_lifecycle() -> None:
    """O lifecycle e um global do processo; cada teste parte do zero."""
    import botgitgud.analysis.cold_build as cold_build_module

    cold_build_module._cold_lifecycle = None


def _result(state: CohortState, *, planned: int = 100, completed: int = 100) -> BucketBuildResult:
    return BucketBuildResult(
        bucket_id=7,
        duration_min_s=480.0,
        duration_max_s=505.0,
        n_members=completed,
        cohort_id="380ec0ee516f8f8a",
        state=state,
        planned=planned,
        completed=completed,
    )


# -- Bug 1: estado -> exit code -----------------------------------------------------


def test_single_ready_bucket_exits_zero() -> None:
    assert build_cohort_exit_code([_result(CohortState.READY)]) == 0


def test_single_deferred_bucket_exits_75() -> None:
    assert build_cohort_exit_code([_result(CohortState.DEFERRED_BUDGET, completed=43)]) == 75


def test_single_failed_bucket_exits_nonzero_hard_failure() -> None:
    code = build_cohort_exit_code([_result(CohortState.FAILED, completed=0)])
    assert code not in (0, EX_TEMPFAIL)
    assert code != 0


def test_multiple_ready_buckets_exit_zero() -> None:
    assert build_cohort_exit_code([_result(CohortState.READY), _result(CohortState.READY)]) == 0


def test_any_deferred_bucket_makes_the_whole_run_deferred() -> None:
    results = [_result(CohortState.READY), _result(CohortState.DEFERRED_BUDGET, completed=10)]
    assert build_cohort_exit_code(results) == EX_TEMPFAIL


def test_hard_failure_dominates_a_deferred_bucket() -> None:
    results = [_result(CohortState.DEFERRED_BUDGET, completed=10), _result(CohortState.FAILED)]
    assert build_cohort_exit_code(results) == 1


def test_no_eligible_bucket_is_not_success() -> None:
    assert build_cohort_exit_code([]) == 1


# -- Bug 1: mensagens honestas -------------------------------------------------------


def test_deferred_message_never_claims_the_cohort_was_built() -> None:
    text = cli_module._render_bucket(_result(CohortState.DEFERRED_BUDGET, completed=43))
    assert "construída" not in text
    assert "construida" not in text
    assert "Cohort ready" not in text


def test_deferred_message_shows_planned_completed_remaining_and_resume() -> None:
    text = cli_module._render_bucket(_result(CohortState.DEFERRED_BUDGET, completed=43))
    assert "planned: 100" in text
    assert "completed: 43" in text
    assert "remaining: 57" in text
    assert "resume: execute the same command after budget reset" in text


def test_ready_message_states_ready_with_matching_counts() -> None:
    text = cli_module._render_bucket(_result(CohortState.READY))
    assert "Cohort ready" in text
    assert "planned: 100" in text
    assert "completed: 100" in text


def test_failed_message_is_distinct_from_both() -> None:
    text = cli_module._render_bucket(_result(CohortState.FAILED, completed=0))
    assert "failed" in text.lower()
    assert "Cohort ready" not in text
    assert "resume:" not in text


# -- Bug 2: o processo batch publica o snapshot ---------------------------------------


class _Budget:
    points_remaining = 2600.0
    points_limit = 3600.0


def test_publisher_writes_a_snapshot_on_enter(tmp_path: Path) -> None:
    with ColdBuildPublisher(tmp_path, budget=_Budget(), interval_s=60.0):
        snapshot = read_snapshot(tmp_path)
        assert snapshot is not None
        assert snapshot.cold_build is not None
        assert snapshot.cold_build["mode"] == "prewarm"


def test_snapshot_carries_progress_and_budget(tmp_path: Path) -> None:
    with ColdBuildPublisher(tmp_path, budget=_Budget(), interval_s=60.0) as pub:
        pub.record(
            "building",
            "380ec0ee516f8f8a",
            buckets=[
                {
                    "cohort_id": "380ec0ee516f8f8a",
                    "state": "deferred_budget",
                    "planned": 100,
                    "completed": 97,
                    "remaining": 3,
                }
            ],
        )
        snapshot = read_snapshot(tmp_path)
    assert snapshot is not None
    cold = snapshot.cold_build
    assert cold is not None
    assert cold["stage"] == "building"
    assert cold["buckets"][0]["planned"] == 100
    assert cold["buckets"][0]["completed"] == 97
    assert cold["buckets"][0]["remaining"] == 3
    assert snapshot.points_remaining == 2600.0
    assert snapshot.points_limit == 3600.0


def test_final_snapshot_survives_the_process_and_explains_the_outcome(tmp_path: Path) -> None:
    with ColdBuildPublisher(tmp_path, budget=_Budget(), interval_s=60.0) as pub:
        pub.record("deferred_budget", outcome="deferred_budget")
    snapshot = read_snapshot(tmp_path)  # depois do __exit__
    assert snapshot is not None
    assert snapshot.cold_build is not None
    assert snapshot.cold_build["outcome"] == "deferred_budget"


@pytest.mark.parametrize("outcome", ["ready", "deferred_budget", "failed"])
def test_every_terminal_outcome_is_readable_afterwards(tmp_path: Path, outcome: str) -> None:
    with ColdBuildPublisher(tmp_path, budget=_Budget(), interval_s=60.0) as pub:
        pub.record(outcome, outcome=outcome)
    snapshot = read_snapshot(tmp_path)
    assert snapshot is not None and snapshot.cold_build is not None
    assert snapshot.cold_build["outcome"] == outcome


def test_snapshot_write_is_atomic_and_leaves_no_partial_file(tmp_path: Path) -> None:
    with ColdBuildPublisher(tmp_path, budget=_Budget(), interval_s=60.0) as pub:
        for i in range(5):
            pub.record("building", tick=i)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [SNAPSHOT_FILENAME]
    json.loads((tmp_path / SNAPSHOT_FILENAME).read_text(encoding="utf-8"))


def test_snapshot_is_fresh_while_the_build_runs(tmp_path: Path) -> None:
    with ColdBuildPublisher(tmp_path, budget=_Budget(), interval_s=60.0) as pub:
        pub.record("building")
        snapshot = read_snapshot(tmp_path)
        assert snapshot is not None
        assert snapshot.is_stale is False


def test_ops_status_reads_prewarm_progress_without_touching_duckdb(tmp_path: Path) -> None:
    """D-34: a observabilidade passa pelo processo dono — nenhuma conexao
    externa ao DuckDB, que nem existe neste tmp_path.
    """
    with ColdBuildPublisher(tmp_path, budget=_Budget(), interval_s=60.0) as pub:
        pub.record(
            "building",
            "380ec0ee516f8f8a",
            buckets=[{"cohort_id": "380ec0ee516f8f8a", "planned": 100, "completed": 43}],
        )
    assert not (tmp_path / "warehouse.duckdb").exists()
    lines = warehouse_status(tmp_path)
    assert lines is not None
    text = "\n".join(lines)
    assert "prewarm" in text
    assert "380ec0ee516f8f8a" in text


def test_publisher_never_kills_the_build_when_the_disk_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import botgitgud.bot.ops_snapshot as snap_module

    monkeypatch.setattr(
        snap_module,
        "write_snapshot",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("disco cheio")),
    )
    with ColdBuildPublisher(tmp_path, budget=_Budget(), interval_s=60.0) as pub:
        pub.record("building")  # nao levanta


# -- partial nunca vira READY ----------------------------------------------------------


def test_partial_progress_is_never_reported_as_ready() -> None:
    partial = _result(CohortState.DEFERRED_BUDGET, completed=97)
    assert partial.is_ready is False
    assert build_cohort_exit_code([partial]) == EX_TEMPFAIL
    assert "Cohort ready" not in cli_module._render_bucket(partial)


# -- integracao do comando (sem rede) ---------------------------------------------------


def _args(tmp_path: Path) -> Any:
    return SimpleNamespace(
        encounter=3183, klass="Warlock", spec="Demonology", difficulty=5, duration_bucket=493.312
    )


def test_command_returns_75_and_publishes_when_the_build_defers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    deps = SimpleNamespace(
        store=SimpleNamespace(close=lambda: None),
        client=SimpleNamespace(close=lambda: None, points_remaining=1500.0, points_limit=3600.0),
        settings=SimpleNamespace(data_dir=tmp_path),
    )
    monkeypatch.setattr(cli_module, "Settings", lambda: SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(cli_module, "_build_deps", lambda _s: deps)
    monkeypatch.setattr(
        cli_module,
        "build_cohorts",
        lambda *_a, **_k: (_ for _ in ()).throw(
            CohortDeferredBudget(
                "sem orçamento",
                cohort_id="380ec0ee516f8f8a",
                estimated_api_points=2128.0,
                available_api_points=1500.0,
                protected_floor=1000.0,
                safety_margin=100.0,
            )
        ),
    )

    code = cli_module._cmd_build_cohort(_args(tmp_path))

    assert code == EX_TEMPFAIL
    err = capsys.readouterr().err
    assert "deferred by WCL budget" in err
    assert "resume: execute the same command after budget reset" in err
    assert "construída" not in err
    snapshot = read_snapshot(tmp_path)
    assert snapshot is not None and snapshot.cold_build is not None
    assert snapshot.cold_build["outcome"] == "deferred_budget"


def test_command_returns_zero_and_publishes_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    deps = SimpleNamespace(
        store=SimpleNamespace(close=lambda: None),
        client=SimpleNamespace(close=lambda: None, points_remaining=3000.0, points_limit=3600.0),
        settings=SimpleNamespace(data_dir=tmp_path),
    )
    monkeypatch.setattr(cli_module, "Settings", lambda: SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(cli_module, "_build_deps", lambda _s: deps)
    monkeypatch.setattr(cli_module, "build_cohorts", lambda *_a, **_k: [_result(CohortState.READY)])

    code = cli_module._cmd_build_cohort(_args(tmp_path))

    assert code == 0
    out = capsys.readouterr().out
    assert "Cohort ready" in out
    snapshot = read_snapshot(tmp_path)
    assert snapshot is not None and snapshot.cold_build is not None
    assert snapshot.cold_build["outcome"] == "ready"


def test_command_returns_75_for_a_partially_completed_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A regressao direta do Bug 1: antes isto retornava 0."""
    deps = SimpleNamespace(
        store=SimpleNamespace(close=lambda: None),
        client=SimpleNamespace(close=lambda: None, points_remaining=1500.0, points_limit=3600.0),
        settings=SimpleNamespace(data_dir=tmp_path),
    )
    monkeypatch.setattr(cli_module, "Settings", lambda: SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(cli_module, "_build_deps", lambda _s: deps)
    monkeypatch.setattr(
        cli_module,
        "build_cohorts",
        lambda *_a, **_k: [_result(CohortState.DEFERRED_BUDGET, completed=43)],
    )

    code = cli_module._cmd_build_cohort(_args(tmp_path))

    assert code == EX_TEMPFAIL
    err = capsys.readouterr().err
    assert "completed: 43" in err
    assert "remaining: 57" in err
    assert "coorte(s) pronta(s)" not in err
