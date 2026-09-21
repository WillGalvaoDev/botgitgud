from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from botgitgud import cli_experiment_evaluate
from botgitgud.cli import build_parser
from botgitgud.cli_experiment_evaluate import cmd_experiment_evaluate
from botgitgud.domain.models import AbilityDamage, FightRef, PlayerBuild, PlayerLog
from botgitgud.ingest.discovery_store import DiscoveryStore
from botgitgud.ingest.store import Store
from botgitgud.phase4.experiment import ExperimentBudget
from botgitgud.phase4.experiment_campaign import (
    ExperimentCampaign,
    PlannedExperimentObservation,
    StatisticalExperimentPlan,
)
from botgitgud.phase4.experiment_store import (
    CollectionStatus,
    ExperimentCampaignStore,
    StoredCampaign,
)

ENCOUNTER = 3176
DIFFICULTY = 5
PARTITION = 4


# -- argument parsing -----------------------------------------------------------


def test_experiment_evaluate_requires_campaign() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["experiment-evaluate"])


def test_experiment_evaluate_parses_optional_filters() -> None:
    args = build_parser().parse_args(
        [
            "experiment-evaluate",
            "--campaign",
            "exp-abc123",
            "--granularity",
            "model_global",
            "--split",
            "s1_temporal_within_target",
        ]
    )
    assert args.campaign == "exp-abc123"
    assert args.granularity == "model_global"
    assert args.split == "s1_temporal_within_target"


def test_experiment_evaluate_rejects_unknown_granularity() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "experiment-evaluate",
                "--campaign",
                "exp-abc123",
                "--granularity",
                "model_hierarchical",
            ]
        )


# -- module never imports a WCL client: zero WCL calls by construction -----------


def test_module_never_imports_wcl_client() -> None:
    import inspect

    source = inspect.getsource(cli_experiment_evaluate)
    assert "from botgitgud.wcl" not in source
    assert "import botgitgud.wcl" not in source
    assert "WclClient(" not in source


# -- fixtures ----------------------------------------------------------------------


def _player_log(*, report_code: str, fight_id: int, player: str, index: int) -> PlayerLog:
    fight = FightRef(
        report_code=report_code,
        fight_id=fight_id,
        encounter_id=ENCOUNTER,
        boss_name="Test Boss",
        difficulty=DIFFICULTY,
        duration_s=300.0 + index,
        kill=True,
        partition=PARTITION,
    )
    build = PlayerBuild(
        character_name=player,
        server="Azralon",
        class_name="Mage",
        spec_name="Frost",
        role="dps",
        item_level=480.0 + index,
        talent_hash=None,
        tier_pieces=index % 4,
        external_buffs=frozenset({10060}),
        has_augmentation=bool(index % 2),
    )
    return PlayerLog(
        fight=fight,
        build=build,
        dps=150_000.0,
        percentile=float((index * 7) % 101),
        cast_timeline={104316: tuple(float(t) for t in range(index % 5 + 1))},
        active_time_pct=0.9,
        damage_by_ability={104316: AbilityDamage(104316, 5_000_000.0, 30, 3)},
        uptimes={395152: 0.5 + (index % 10) / 20.0},
        resource_waste={"Mana": float(index % 10)},
        deaths=index % 3,
        downtime_s=float(index % 15),
        avg_targets_per_cast={104316: 1.0 + (index % 3) / 2.0},
    )


def _seed_completed_campaign(store: Store, *, n: int) -> str:
    """Freezes a synthetic campaign of `n` observations, marks every one
    `completed` and writes a matching log for each — the minimum needed for
    `ExperimentalDatasetBuilder.build(campaign_id=...)` to find real rows.
    """
    observations = tuple(
        PlannedExperimentObservation(
            report_code=f"R{i:04d}",
            fight_id=i,
            player_name=f"P{i:04d}",
            class_name="Mage",
            spec_name="Frost",
            encounter_id=ENCOUNTER,
            difficulty=DIFFICULTY,
            partition=PARTITION,
            rank_percent=float((i * 7) % 101),
            bucket="00-20",
            start_time_ms=1_000 + i,
        )
        for i in range(n)
    )
    campaign = ExperimentCampaign(
        request=StatisticalExperimentPlan(
            partition=PARTITION,
            difficulties=frozenset({DIFFICULTY}),
            budget=ExperimentBudget(max_api_points=10_000.0),
            max_observations=n,
        ),
        observations=observations,
        candidates_available=n,
        strata_total=1,
        strata_covered=1,
        estimated_api_points=100.0,
        stopped_reason="max_observations",
    )
    DiscoveryStore(store)  # creates discovery_fights/discovery_reports, left-joined below
    campaigns = ExperimentCampaignStore(store)
    stored = campaigns.freeze(campaign)
    for planned in observations:
        store.write_log(
            _player_log(
                report_code=planned.report_code,
                fight_id=planned.fight_id,
                player=planned.player_name,
                index=planned.fight_id,
            )
        )
        campaigns.finish(
            stored.campaign_id,
            _ordinal(stored, planned),
            status=CollectionStatus.COMPLETED,
            points=5.0,
            output="ok",
        )
    return stored.campaign_id


def _ordinal(stored: StoredCampaign, planned: PlannedExperimentObservation) -> int:
    for item in stored.observations:
        same_report = item.planned.report_code == planned.report_code
        same_fight = item.planned.fight_id == planned.fight_id
        if same_report and same_fight:
            return item.ordinal
    raise AssertionError("observation not found in frozen campaign")


def _args(
    campaign: str, granularity: str | None = None, split: str | None = None
) -> argparse.Namespace:
    return argparse.Namespace(campaign=campaign, granularity=granularity, split=split)


# -- behavior ------------------------------------------------------------------------


def test_unknown_campaign_returns_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with Store(tmp_path) as store:
        code = cmd_experiment_evaluate(_args("exp-doesnotexist"), build_store=lambda _s: store)
    assert code == 1
    assert "desconhecida" in capsys.readouterr().err


def test_empty_dataset_returns_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with Store(tmp_path) as store:
        DiscoveryStore(store)
        campaign = ExperimentCampaign(
            request=StatisticalExperimentPlan(
                partition=PARTITION,
                difficulties=frozenset({DIFFICULTY}),
                budget=ExperimentBudget(max_api_points=10_000.0),
            ),
            observations=(),
            candidates_available=0,
            strata_total=0,
            strata_covered=0,
            estimated_api_points=0.0,
            stopped_reason="pool_exhausted",
        )
        stored = ExperimentCampaignStore(store).freeze(campaign)
        code = cmd_experiment_evaluate(_args(stored.campaign_id), build_store=lambda _s: store)
    assert code == 1
    assert "completed" in capsys.readouterr().err


def test_full_run_is_offline_and_reports_partial_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with Store(tmp_path) as setup_store:
        campaign_id = _seed_completed_campaign(setup_store, n=30)

    # cmd_experiment_evaluate closes whatever build_store hands it (same
    # contract as the other cli_experiment.py commands), so it needs its
    # own connection rather than reusing setup_store's.
    code = cmd_experiment_evaluate(
        _args(campaign_id, granularity="model_global", split="s1_temporal_within_target"),
        build_store=lambda _s: Store(tmp_path),
    )
    out = capsys.readouterr().out

    with Store(tmp_path) as verify_store:
        runs = verify_store.execute_returning(
            "SELECT campaign_id FROM experiment_architecture_eval_runs WHERE campaign_id = ?",
            [campaign_id],
        )

    assert code == 0
    assert "30 / 30" in out  # n == planned here: dataset is COMPLETE, not PARTIAL
    assert "API points consumidos ............... 0" in out
    assert "phase4_model_registry" in out
    assert len(runs) == 1
