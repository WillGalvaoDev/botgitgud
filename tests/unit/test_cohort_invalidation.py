from __future__ import annotations

import hashlib
import logging
from itertools import permutations
from pathlib import Path
from typing import Any

import duckdb
import pytest
import structlog

from botgitgud.analysis.cohort_invalidation import assess_cohort_pool, plan_invalidation
from botgitgud.cli import main


@pytest.fixture(autouse=True)
def _restore_logging() -> Any:
    """Restore process-global logging state after the CLI dry-run test."""
    root = logging.getLogger()
    previous = list(root.handlers)
    previous_level = root.level
    yield
    root.handlers = previous
    root.setLevel(previous_level)
    structlog.reset_defaults()


def _row(
    cohort_id: str,
    *,
    difficulty: int = 5,
    partition: int = 3,
    n_members: int = 8,
    state: str = "ready",
) -> dict[str, object]:
    return {
        "cohort_id": cohort_id,
        "difficulty": difficulty,
        "partition": partition,
        "n_members": n_members,
        "state": state,
    }


def test_assessment_classifies_valid_incomplete_and_semantically_invalid() -> None:
    assert assess_cohort_pool(_row("valid")).status == "valid"
    assert assess_cohort_pool(_row("small", n_members=7)).status == "incomplete"
    assert assess_cohort_pool(_row("building", state="deferred_budget")).status == "incomplete"
    semantic = assess_cohort_pool(_row("heroic", difficulty=4))
    assert semantic.status == "semantically_invalid"


def test_semantic_invalidity_wins_and_retains_both_reasons() -> None:
    result = assess_cohort_pool(_row("both", difficulty=4, state="failed"))
    assert result.status == "semantically_invalid"
    assert "declared_difficulty_is_not_mythic" in result.reasons
    assert "cohort_state_is_not_ready" in result.reasons


def test_reasons_empty_exactly_for_valid() -> None:
    results = [
        assess_cohort_pool(_row("valid")),
        assess_cohort_pool(_row("small", n_members=1)),
        assess_cohort_pool(_row("heroic", difficulty=4)),
    ]
    assert all((not result.reasons) == (result.status == "valid") for result in results)


def test_plan_selects_only_incompatible_and_preserves_every_mythic_pool() -> None:
    plan = plan_invalidation([_row("m1"), _row("h", difficulty=4), _row("m2", n_members=1)])
    assert [item.cohort_id for item in plan.to_invalidate] == ["h"]
    assert {item.cohort_id for item in plan.preserved} == {"m1", "m2"}
    assert plan.counts_by_difficulty_partition == ((4, 3, 1),)


def test_plan_is_deterministic_under_input_permutation() -> None:
    rows = [_row("z", difficulty=4), _row("a"), _row("b", difficulty=4, partition=2)]
    plans = [plan_invalidation(order) for order in permutations(rows)]
    assert all(plan == plans[0] for plan in plans[1:])


def test_dry_run_opens_database_read_only_and_does_not_change_it(
    tmp_path: Path,
    capsys: object,
) -> None:
    database = tmp_path / "warehouse.duckdb"
    connection = duckdb.connect(str(database))
    connection.execute(
        """CREATE TABLE cohort_registry (
        cohort_id VARCHAR, difficulty INTEGER, partition INTEGER, n_members INTEGER)"""
    )
    connection.execute(
        "INSERT INTO cohort_registry VALUES ('heroic', 4, 3, 8), ('mythic', 5, 3, 8)"
    )
    connection.close()
    before = hashlib.sha256(database.read_bytes()).digest()

    assert main(["plan-cohort-invalidation", "--database", str(database), "--json"]) == 0

    after = hashlib.sha256(database.read_bytes()).digest()
    assert after == before
    # Keep capsys in the signature so CLI output is captured, never leaked to the suite.
    assert capsys is not None
