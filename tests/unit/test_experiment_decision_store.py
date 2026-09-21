from __future__ import annotations

from pathlib import Path

from botgitgud.domain.specs import SpecId
from botgitgud.ingest.store import Store
from botgitgud.phase4.experiment_decision import FoldPredictions, GroupHoldoutResult, HoldoutStatus
from botgitgud.phase4.experiment_decision_store import (
    ExperimentArchitectureDecisionStore,
    holdout_results_to_json,
)
from botgitgud.phase4.experiment_models import FeatureFamily
from botgitgud.phase4.registry import ModelStatus, Phase4ModelRecord, Phase4ModelRegistry
from botgitgud.phase4.target import Phase4Target

CAMPAIGN_ID = "exp-decidestoretest000"


def _payload() -> dict[str, object]:
    holdout = [
        GroupHoldoutResult(
            key="Mage/Frost",
            n_train=100,
            n_validation=20,
            status=HoldoutStatus.OK,
            by_family={
                FeatureFamily.F1_CONTROLLABLE_ONLY: FoldPredictions(
                    y_true=(10.0, 20.0),
                    predictions={"baseline_0": (15.0, 15.0), "lightgbm": (12.0, 18.0)},
                )
            },
        )
    ]
    return {"spec_holdout": holdout_results_to_json(holdout), "coverage": {"current_specs": 25}}


def test_save_and_list_round_trips(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        decide_store = ExperimentArchitectureDecisionStore(store)
        decide_store.save(
            "decide-001",
            campaign_id=CAMPAIGN_ID,
            dataset_fingerprint="ds-abc",
            dataset_row_count=603,
            seed=20260821,
            payload=_payload(),
        )
        runs = decide_store.list_runs(CAMPAIGN_ID)
    assert len(runs) == 1
    assert runs[0]["run_id"] == "decide-001"
    assert runs[0]["dataset_fingerprint"] == "ds-abc"
    assert runs[0]["dataset_row_count"] == 603
    assert runs[0]["seed"] == 20260821


def test_save_is_idempotent_for_the_same_run_id(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        decide_store = ExperimentArchitectureDecisionStore(store)
        decide_store.save(
            "decide-001",
            campaign_id=CAMPAIGN_ID,
            dataset_fingerprint="ds-abc",
            dataset_row_count=603,
            seed=1,
            payload=_payload(),
        )
        decide_store.save(
            "decide-001",
            campaign_id=CAMPAIGN_ID,
            dataset_fingerprint="ds-abc",
            dataset_row_count=603,
            seed=1,
            payload=_payload(),
        )
        runs = decide_store.list_runs(CAMPAIGN_ID)
    assert len(runs) == 1


def test_holdout_results_serialize_raw_predictions_for_reanalysis() -> None:
    holdout = [
        GroupHoldoutResult(
            key="Mage/Frost",
            n_train=100,
            n_validation=2,
            status=HoldoutStatus.OK,
            by_family={},
        )
    ]
    rows = holdout_results_to_json(holdout)
    assert rows[0]["key"] == "Mage/Frost"
    assert rows[0]["status"] == "ok"
    assert rows[0]["by_family"] == {}


def test_decision_store_and_model_registry_are_fully_isolated(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        decide_store = ExperimentArchitectureDecisionStore(store)
        registry = Phase4ModelRegistry(store)

        decide_store.save(
            "decide-001",
            campaign_id=CAMPAIGN_ID,
            dataset_fingerprint="ds-abc",
            dataset_row_count=603,
            seed=1,
            payload=_payload(),
        )
        target = Phase4Target(SpecId("Mage", "Frost"), 3176, 5, 4)
        registry.register(Phase4ModelRecord(target=target, status=ModelStatus.COLLECTING))

        assert len(registry.list_all()) == 1
        assert len(decide_store.list_runs(CAMPAIGN_ID)) == 1

        tables = {
            row[0]
            for row in store.execute_returning(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name IN ('phase4_model_registry', "
                "'experiment_architecture_decision_runs', 'experiment_architecture_eval_runs')"
            )
        }
        assert "phase4_model_registry" in tables
        assert "experiment_architecture_decision_runs" in tables


def test_no_model_from_a_decision_run_is_ever_ready_in_the_registry(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        decide_store = ExperimentArchitectureDecisionStore(store)
        decide_store.save(
            "decide-001",
            campaign_id=CAMPAIGN_ID,
            dataset_fingerprint="ds-abc",
            dataset_row_count=603,
            seed=1,
            payload=_payload(),
        )
        registry = Phase4ModelRegistry(store)
        assert registry.list_all() == []
