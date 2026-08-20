from __future__ import annotations

import time
from pathlib import Path

from botgitgud.analysis.dataset_status import (
    GATE_TARGET,
    TEMPORAL_MIN_PER_SIDE,
    TargetStatus,
    target_status,
    top_candidate_groups,
)
from botgitgud.domain.models import AbilityDamage, FightRef, PlayerBuild, PlayerLog
from botgitgud.ingest.discovery_store import DiscoveryStore
from botgitgud.ingest.fight_rankings import DpsRanking, FightRankings
from botgitgud.ingest.store import Store

ENCOUNTER = 3179
DIFFICULTY = 5
PARTITION = 3
CLASS = "DeathKnight"
SPEC = "Unholy"


def _fight(**overrides: object) -> FightRef:
    defaults: dict[str, object] = {
        "report_code": "ABCDEFGHIJKLMNOP",
        "fight_id": 1,
        "encounter_id": ENCOUNTER,
        "boss_name": "Fallen-King Salhadaar",
        "difficulty": DIFFICULTY,
        "duration_s": 300.0,
        "kill": True,
        "partition": PARTITION,
    }
    defaults.update(overrides)
    return FightRef(**defaults)  # type: ignore[arg-type]


def _build(character_name: str = "Player1", **overrides: object) -> PlayerBuild:
    defaults: dict[str, object] = {
        "character_name": character_name,
        "server": "Kazzak",
        "class_name": CLASS,
        "spec_name": SPEC,
        "role": "dps",
        "item_level": 290.0,
        "talent_hash": None,
        "tier_pieces": None,
    }
    defaults.update(overrides)
    return PlayerBuild(**defaults)  # type: ignore[arg-type]


def _log(
    character_name: str = "Player1",
    *,
    percentile: float | None = 50.0,
    active_time_pct: float | None = 0.9,
    fight_overrides: dict[str, object] | None = None,
) -> PlayerLog:
    return PlayerLog(
        fight=_fight(**(fight_overrides or {})),
        build=_build(character_name),
        dps=100000.0,
        percentile=percentile,
        cast_timeline={104316: (1.0,)},
        active_time_pct=active_time_pct,
        damage_by_ability={104316: AbilityDamage(104316, 500_000.0, 17, 17)},
    )


def _fight_rankings(
    fight_id: int = 1, *, partition: int | None = PARTITION, kill: bool = True, dps: tuple = ()
) -> FightRankings:
    return FightRankings(
        fight_id=fight_id,
        partition=partition,
        encounter_id=ENCOUNTER,
        difficulty=DIFFICULTY,
        size=20,
        kill=kill,
        duration_s=300.0,
        dps=dps,
    )


def _dps_ranking(
    name: str = "Player1", *, class_name: str = CLASS, spec_name: str = SPEC
) -> DpsRanking:
    return DpsRanking(
        player_name=name,
        server_name="Kazzak",
        server_region="EU",
        class_name=class_name,
        spec_name=spec_name,
        amount=100000.0,
        rank_percent=50,
        bracket_data=290,
        total_parses=100,
    )


# -- top_candidate_groups: general view ---------------------------------------


def test_top_candidate_groups_counts_and_ranks_by_size(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_fight_rankings(
            _fight_rankings(1, dps=(_dps_ranking("A"), _dps_ranking("B"))), report_code="CODE1"
        )
        discovery.write_fight_rankings(
            _fight_rankings(2, dps=(_dps_ranking("C", class_name="Mage", spec_name="Fire"),)),
            report_code="CODE2",
        )

        groups = top_candidate_groups(store)

    assert len(groups) == 2
    assert groups[0].class_name == CLASS
    assert groups[0].n_candidates == 2
    assert groups[1].class_name == "Mage"
    assert groups[1].n_candidates == 1


def test_top_candidate_groups_excludes_non_kill_fights(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_fight_rankings(
            _fight_rankings(1, kill=False, dps=(_dps_ranking(),)), report_code="CODE1"
        )

        groups = top_candidate_groups(store)

    assert groups == []


def test_top_candidate_groups_respects_limit(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        for i in range(5):
            discovery.write_fight_rankings(
                _fight_rankings(
                    i, dps=(_dps_ranking("P", class_name=f"Class{i}", spec_name="Spec"),)
                ),
                report_code=f"CODE{i}",
            )

        groups = top_candidate_groups(store, limit=2)

    assert len(groups) == 2


# -- target_status: reports/fights/candidates from discovery -----------------


def test_target_status_counts_reports_fights_and_candidates(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_report(report_code="CODE1", zone_id=46, start_time_ms=0, end_time_ms=1)
        discovery.write_report(report_code="CODE2", zone_id=46, start_time_ms=0, end_time_ms=1)
        discovery.write_fight_rankings(
            _fight_rankings(1, dps=(_dps_ranking("A"), _dps_ranking("B"))), report_code="CODE1"
        )

        status = target_status(
            store,
            discovery,
            class_name=CLASS,
            spec_name=SPEC,
            encounter_id=ENCOUNTER,
            difficulty=DIFFICULTY,
            partition=PARTITION,
        )

    assert status.reports_discovered == 2
    assert status.fights_triaged == 1
    assert status.candidates == 2


def test_target_status_candidates_exclude_non_kill_and_other_spec(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_fight_rankings(
            _fight_rankings(1, kill=False, dps=(_dps_ranking(),)), report_code="CODE1"
        )
        discovery.write_fight_rankings(
            _fight_rankings(2, dps=(_dps_ranking(class_name="Mage", spec_name="Fire"),)),
            report_code="CODE2",
        )

        status = target_status(
            store,
            discovery,
            class_name=CLASS,
            spec_name=SPEC,
            encounter_id=ENCOUNTER,
            difficulty=DIFFICULTY,
            partition=PARTITION,
        )

    assert status.candidates == 0


# -- target_status: validity contract (§10.2) over the `logs` table ----------


def test_target_status_valid_log_counted(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        store.write_log(_log())

        status = target_status(
            store,
            discovery,
            class_name=CLASS,
            spec_name=SPEC,
            encounter_id=ENCOUNTER,
            difficulty=DIFFICULTY,
            partition=PARTITION,
        )

    assert status.ingested == 1
    assert status.valid == 1
    assert status.rejected == 0


def test_target_status_rejects_partition_mismatch(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        store.write_log(_log(fight_overrides={"partition": 4}))

        status = target_status(
            store,
            discovery,
            class_name=CLASS,
            spec_name=SPEC,
            encounter_id=ENCOUNTER,
            difficulty=DIFFICULTY,
            partition=PARTITION,
        )

    assert status.valid == 0
    assert status.rejected_by_reason == {"partition_divergente": 1}


def test_target_status_rejects_missing_percentile(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        store.write_log(_log(percentile=None))

        status = target_status(
            store,
            discovery,
            class_name=CLASS,
            spec_name=SPEC,
            encounter_id=ENCOUNTER,
            difficulty=DIFFICULTY,
            partition=PARTITION,
        )

    assert status.rejected_by_reason == {"percentile_ausente": 1}


def test_target_status_rejects_non_kill(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        store.write_log(_log(fight_overrides={"kill": False}))

        status = target_status(
            store,
            discovery,
            class_name=CLASS,
            spec_name=SPEC,
            encounter_id=ENCOUNTER,
            difficulty=DIFFICULTY,
            partition=PARTITION,
        )

    assert status.rejected_by_reason == {"fight_nao_kill": 1}


def test_target_status_rejects_missing_active_time_pct(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        store.write_log(_log(active_time_pct=None))

        status = target_status(
            store,
            discovery,
            class_name=CLASS,
            spec_name=SPEC,
            encounter_id=ENCOUNTER,
            difficulty=DIFFICULTY,
            partition=PARTITION,
        )

    assert status.rejected_by_reason == {"feature_incompleta": 1}


def test_target_status_rejects_duration_outside_sanity_band(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        # Median comes from every log at the target partition. 5 logs at
        # 300s (median=300) + 1 wild outlier at 900s (200% over median,
        # outside the +-35% sanity band).
        for i in range(5):
            store.write_log(_log(character_name=f"P{i}"))
        store.write_log(_log(character_name="Outlier", fight_overrides={"duration_s": 900.0}))

        status = target_status(
            store,
            discovery,
            class_name=CLASS,
            spec_name=SPEC,
            encounter_id=ENCOUNTER,
            difficulty=DIFFICULTY,
            partition=PARTITION,
        )

    assert status.valid == 5
    assert status.rejected_by_reason == {"duracao_fora_da_banda": 1}


def test_target_status_counts_duplicates_and_reads_latest(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        store.write_log(_log(percentile=None))  # invalid ingestion first
        time.sleep(0.01)
        store.write_log(_log(percentile=60.0))  # re-ingested, now valid — same natural key

        status = target_status(
            store,
            discovery,
            class_name=CLASS,
            spec_name=SPEC,
            encounter_id=ENCOUNTER,
            difficulty=DIFFICULTY,
            partition=PARTITION,
        )

    assert status.ingested == 1  # deduped to the latest row
    assert status.valid == 1  # the latest (percentile=60.0) wins
    assert status.duplicates == 1  # 2 raw rows - 1 deduped row


def test_target_status_temporal_split_false_below_two_thousand_valid(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        store.write_log(_log())

        status = target_status(
            store,
            discovery,
            class_name=CLASS,
            spec_name=SPEC,
            encounter_id=ENCOUNTER,
            difficulty=DIFFICULTY,
            partition=PARTITION,
        )

    assert status.temporal_split_ok is False
    assert status.gate_pass is False


# -- TargetStatus: pure dataclass math (P1/P2 gate logic, no Store needed) ---


def _status(**overrides: object) -> TargetStatus:
    defaults: dict[str, object] = {
        "class_name": CLASS,
        "spec_name": SPEC,
        "encounter_id": ENCOUNTER,
        "difficulty": DIFFICULTY,
        "partition": PARTITION,
        "reports_discovered": 0,
        "fights_triaged": 0,
        "candidates": 0,
        "ingested": 0,
        "valid": 0,
        "duplicates": 0,
    }
    defaults.update(overrides)
    return TargetStatus(**defaults)  # type: ignore[arg-type]


def test_gate_pass_requires_both_p1_and_p2() -> None:
    assert _status(valid=GATE_TARGET, temporal_split_ok=True).gate_pass is True
    assert _status(valid=GATE_TARGET - 1, temporal_split_ok=True).gate_pass is False  # P1 fails
    assert _status(valid=GATE_TARGET, temporal_split_ok=False).gate_pass is False  # P2 fails


def test_observations_remaining_never_negative() -> None:
    assert _status(valid=GATE_TARGET + 500).observations_remaining == 0
    assert _status(valid=GATE_TARGET - 100).observations_remaining == 100


def test_progress_pct_caps_at_one_hundred() -> None:
    assert _status(valid=GATE_TARGET).progress_pct == 100.0
    assert _status(valid=GATE_TARGET * 2).progress_pct == 100.0
    assert _status(valid=GATE_TARGET // 2).progress_pct == 50.0


def test_rejected_sums_every_reason() -> None:
    status = _status(rejected_by_reason={"a": 3, "b": 5})
    assert status.rejected == 8


def test_temporal_min_per_side_matches_the_approved_plan() -> None:
    assert TEMPORAL_MIN_PER_SIDE == 1000


def test_gate_target_matches_the_approved_plan() -> None:
    assert GATE_TARGET == 5000
