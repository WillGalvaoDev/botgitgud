from __future__ import annotations

from pathlib import Path

from botgitgud.domain.specs import SpecId
from botgitgud.ingest.store import Store
from botgitgud.phase4.experiment_calibration_store import ExperimentCalibrationStore
from botgitgud.phase4.registry import ModelStatus, Phase4ModelRecord, Phase4ModelRegistry
from botgitgud.phase4.target import Phase4Target

CAMPAIGN_ID = "exp-calibratestore0000"


def _payload() -> dict[str, object]:
    return {
        "metrics_by_method": {"c0_none": {"mae": 18.0}, "c1_linear": {"mae": 19.0}},
        "observed_bucket": [{"bucket": "00-20", "n": 36}],
    }


def test_save_and_list_round_trips(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        calibration_store = ExperimentCalibrationStore(store)
        calibration_store.save(
            "calibrate-001",
            campaign_id=CAMPAIGN_ID,
            dataset_fingerprint="ds-abc",
            dataset_row_count=603,
            seed=20260821,
            n_train=415,
            n_calibration=93,
            n_validation=95,
            payload=_payload(),
        )
        runs = calibration_store.list_runs(CAMPAIGN_ID)
    assert len(runs) == 1
    assert runs[0]["run_id"] == "calibrate-001"
    assert runs[0]["dataset_fingerprint"] == "ds-abc"
    assert runs[0]["n_train"] == 415
    assert runs[0]["n_calibration"] == 93
    assert runs[0]["n_validation"] == 95


def test_save_is_idempotent_for_the_same_run_id(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        calibration_store = ExperimentCalibrationStore(store)
        for _ in range(2):
            calibration_store.save(
                "calibrate-001",
                campaign_id=CAMPAIGN_ID,
                dataset_fingerprint="ds-abc",
                dataset_row_count=603,
                seed=1,
                n_train=10,
                n_calibration=5,
                n_validation=5,
                payload=_payload(),
            )
        runs = calibration_store.list_runs(CAMPAIGN_ID)
    assert len(runs) == 1


def test_list_runs_only_returns_the_requested_campaign(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        calibration_store = ExperimentCalibrationStore(store)
        calibration_store.save(
            "calibrate-001",
            campaign_id=CAMPAIGN_ID,
            dataset_fingerprint="ds-abc",
            dataset_row_count=603,
            seed=1,
            n_train=10,
            n_calibration=5,
            n_validation=5,
            payload=_payload(),
        )
        assert calibration_store.list_runs("exp-someothercampaign0") == []


def test_calibration_store_and_model_registry_are_fully_isolated(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        calibration_store = ExperimentCalibrationStore(store)
        registry = Phase4ModelRegistry(store)

        calibration_store.save(
            "calibrate-001",
            campaign_id=CAMPAIGN_ID,
            dataset_fingerprint="ds-abc",
            dataset_row_count=603,
            seed=1,
            n_train=10,
            n_calibration=5,
            n_validation=5,
            payload=_payload(),
        )
        target = Phase4Target(SpecId("Mage", "Frost"), 3176, 5, 4)
        registry.register(Phase4ModelRecord(target=target, status=ModelStatus.COLLECTING))

        assert len(registry.list_all()) == 1
        assert len(calibration_store.list_runs(CAMPAIGN_ID)) == 1

        tables = {
            row[0]
            for row in store.execute_returning(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name IN ('phase4_model_registry', 'experiment_calibration_runs', "
                "'experiment_architecture_eval_runs', 'experiment_architecture_decision_runs')"
            )
        }
        assert "phase4_model_registry" in tables
        assert "experiment_calibration_runs" in tables


def test_no_calibrator_from_a_run_is_ever_ready_in_the_registry(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        calibration_store = ExperimentCalibrationStore(store)
        calibration_store.save(
            "calibrate-001",
            campaign_id=CAMPAIGN_ID,
            dataset_fingerprint="ds-abc",
            dataset_row_count=603,
            seed=1,
            n_train=10,
            n_calibration=5,
            n_validation=5,
            payload=_payload(),
        )
        registry = Phase4ModelRegistry(store)
        assert registry.list_all() == []
