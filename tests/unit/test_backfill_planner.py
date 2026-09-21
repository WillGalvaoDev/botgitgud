from __future__ import annotations

from pathlib import Path

from botgitgud.domain.specs import SpecId
from botgitgud.ingest.backfill_planner import BackfillPlan, BackfillPlanner
from botgitgud.ingest.discovery_store import DiscoveryStore
from botgitgud.ingest.store import Store
from botgitgud.phase4.target import Phase4Target


def _target(spec: str = "Unholy", difficulty: int = 5, partition: int = 3) -> Phase4Target:
    return Phase4Target(SpecId("DeathKnight", spec), 3182, difficulty, partition)


def _seed(store: Store) -> None:
    DiscoveryStore(store)
    for index, (spec, difficulty, partition) in enumerate(
        [("Unholy", 5, 3), ("Frost", 5, 3), ("Unholy", 4, 3), ("Unholy", 5, 4)]
    ):
        report = f"REPORT{index:010d}"
        store.execute(
            "INSERT INTO discovery_reports "
            "(report_code, zone_id, start_time_ms, end_time_ms) VALUES (?, 46, ?, ?)",
            [report, 1_700_000_000_000 + index * 1000, 1_700_000_001_000 + index * 1000],
        )
        store.execute(
            "INSERT INTO discovery_fights "
            "(report_code, fight_id, partition, encounter_id, difficulty, kill) "
            "VALUES (?, 1, ?, 3182, ?, true)",
            [report, partition, difficulty],
        )
        store.execute(
            "INSERT INTO discovery_targets "
            "(report_code, fight_id, player_name, class_name, spec_name) "
            "VALUES (?, 1, ?, 'DeathKnight', ?)",
            [report, f"Player{index}", spec],
        )


def test_planner_isolates_spec_partition_and_difficulty_without_external_calls(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    _seed(store)
    planner = BackfillPlanner(store)

    estimates = [
        planner.plan(BackfillPlan(target, 5000))
        for target in (_target(), _target("Frost"), _target(difficulty=4), _target(partition=4))
    ]

    assert [e.candidate_observations for e in estimates] == [1, 1, 1, 1]
    assert [e.observations_planned[0].player_name for e in estimates] == [
        "Player0",
        "Player1",
        "Player2",
        "Player3",
    ]
    assert all(e.observations_remaining == 5000 for e in estimates)
    assert all(e.estimated_api_points == 17.0 for e in estimates)
    assert estimates[0].coverage_start is not None
    store.close()


def test_planner_for_unknown_target_is_empty_and_still_reports_shortfall(tmp_path: Path) -> None:
    store = Store(tmp_path)
    _seed(store)
    estimate = BackfillPlanner(store).plan(
        BackfillPlan(Phase4Target(SpecId("Priest", "Shadow"), 9999, 5, 3))
    )
    assert estimate.candidate_observations == 0
    assert estimate.observations_planned == ()
    assert estimate.observations_remaining == 5000
    assert estimate.estimated_api_points == 0
    store.close()
