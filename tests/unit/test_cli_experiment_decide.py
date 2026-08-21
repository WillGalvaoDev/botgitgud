from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from botgitgud import cli_experiment_decide
from botgitgud.cli import build_parser
from botgitgud.cli_experiment_decide import cmd_experiment_decide
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
from botgitgud.phase4.registry import Phase4ModelRegistry

DIFFICULTY = 5
PARTITION = 4
SPECS = ("Frost", "Fire", "Arcane")
ENCOUNTERS = (3176, 3177)
PER_GROUP = 12  # 3 specs x 2 encounters x 12 = 72 rows; 24/spec, 36/encounter


def _player_log(
    *, report_code: str, fight_id: int, player: str, spec: str, encounter: int, index: int
) -> PlayerLog:
    fight = FightRef(
        report_code=report_code,
        fight_id=fight_id,
        encounter_id=encounter,
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
        spec_name=spec,
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


def _seed_completed_campaign(store: Store, *, seed_discovery: bool = True) -> str:
    observations = []
    i = 0
    for spec in SPECS:
        for encounter in ENCOUNTERS:
            for _ in range(PER_GROUP):
                observations.append(
                    PlannedExperimentObservation(
                        report_code=f"R{i:04d}",
                        fight_id=i,
                        player_name=f"P{i:04d}",
                        class_name="Mage",
                        spec_name=spec,
                        encounter_id=encounter,
                        difficulty=DIFFICULTY,
                        partition=PARTITION,
                        rank_percent=float((i * 7) % 101),
                        bucket="00-20",
                        start_time_ms=1_000 + i,
                    )
                )
                i += 1
    observations = tuple(observations)
    discovery = DiscoveryStore(store)
    if seed_discovery:
        for planned in observations:
            discovery.write_report(
                report_code=planned.report_code,
                zone_id=46,
                start_time_ms=planned.start_time_ms,
                end_time_ms=planned.start_time_ms + 1,
            )
    campaign = ExperimentCampaign(
        request=StatisticalExperimentPlan(
            partition=PARTITION,
            difficulties=frozenset({DIFFICULTY}),
            budget=ExperimentBudget(max_api_points=10_000.0),
            max_observations=len(observations),
        ),
        observations=observations,
        candidates_available=len(observations),
        strata_total=1,
        strata_covered=1,
        estimated_api_points=100.0,
        stopped_reason="max_observations",
    )
    campaigns = ExperimentCampaignStore(store)
    stored = campaigns.freeze(campaign)
    for planned in observations:
        store.write_log(
            _player_log(
                report_code=planned.report_code,
                fight_id=planned.fight_id,
                player=planned.player_name,
                spec=planned.spec_name,
                encounter=planned.encounter_id,
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
        if (
            item.planned.report_code == planned.report_code
            and item.planned.fight_id == planned.fight_id
        ):
            return item.ordinal
    raise AssertionError("observation not found in frozen campaign")


def _args(campaign: str) -> argparse.Namespace:
    return argparse.Namespace(campaign=campaign)


# -- argument parsing -----------------------------------------------------------


def test_experiment_decide_requires_campaign() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["experiment-decide"])


def test_experiment_decide_parses_campaign() -> None:
    args = build_parser().parse_args(["experiment-decide", "--campaign", "exp-abc123"])
    assert args.campaign == "exp-abc123"


# -- module never imports a WCL client: zero WCL calls by construction -----------


def test_module_never_imports_wcl_client() -> None:
    import inspect

    source = inspect.getsource(cli_experiment_decide)
    assert "from botgitgud.wcl" not in source
    assert "import botgitgud.wcl" not in source
    assert "WclClient(" not in source


# -- behavior ------------------------------------------------------------------------


def test_unknown_campaign_returns_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with Store(tmp_path) as store:
        code = cmd_experiment_decide(_args("exp-doesnotexist"), build_store=lambda _s: store)
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
        code = cmd_experiment_decide(_args(stored.campaign_id), build_store=lambda _s: store)
    assert code == 1
    assert "completed" in capsys.readouterr().err


def test_unusable_s1_split_returns_a_clean_error_not_a_crash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without discovery rows every observation shares observed_at_ms=0, so
    S1's temporal cut degenerates to an empty training side — this must
    fail closed with a clear message, never raise past the CLI boundary.
    """
    with Store(tmp_path) as setup_store:
        campaign_id = _seed_completed_campaign(setup_store, seed_discovery=False)

    code = cmd_experiment_decide(_args(campaign_id), build_store=lambda _s: Store(tmp_path))
    assert code == 1
    assert "S1" in capsys.readouterr().err


def test_full_run_is_offline_and_persists_an_artifact(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with Store(tmp_path) as setup_store:
        campaign_id = _seed_completed_campaign(setup_store)

    code = cmd_experiment_decide(_args(campaign_id), build_store=lambda _s: Store(tmp_path))
    out = capsys.readouterr().out

    with Store(tmp_path) as verify_store:
        runs = verify_store.execute_returning(
            "SELECT campaign_id FROM experiment_architecture_decision_runs WHERE campaign_id = ?",
            [campaign_id],
        )
        registry = Phase4ModelRegistry(verify_store)
        registry_rows = registry.list_all()

    assert code == 0
    assert "leave-one-spec-out folds ............. 3" in out
    assert "leave-one-encounter-out folds ........ 2" in out
    assert "API points consumidos ............... 0" in out
    assert "phase4_model_registry" in out
    assert len(runs) == 1
    assert registry_rows == []
