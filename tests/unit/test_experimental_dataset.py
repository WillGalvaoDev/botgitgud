from __future__ import annotations

import time
from pathlib import Path

import pytest

from botgitgud.domain.models import AbilityDamage, FightRef, PlayerBuild, PlayerLog
from botgitgud.ingest.discovery_store import DiscoveryStore
from botgitgud.ingest.fight_rankings import DpsRanking, FightRankings
from botgitgud.ingest.store import Store
from botgitgud.phase4.experiment import MODELLABLE_ROLES, FeatureRole
from botgitgud.phase4.experiment_features import (
    EXCLUDED_LEAKAGE_COLUMNS,
    FEATURE_REGISTRY,
    controllable_feature_names,
    feature_names,
    features_with_role,
    modellable_features,
)
from botgitgud.phase4.experimental_dataset import (
    ExperimentalDatasetBuilder,
    ExperimentalFeatureDataset,
    build_features,
)

ENCOUNTER = 3183
DIFFICULTY = 5
PARTITION = 4


def _player_log(
    *,
    report_code: str = "R0000000000000A",
    fight_id: int = 1,
    player: str = "Zarad",
    class_name: str = "Warlock",
    spec_name: str = "Demonology",
    encounter_id: int = ENCOUNTER,
    difficulty: int = DIFFICULTY,
    partition: int | None = PARTITION,
    percentile: float | None = 55.0,
    duration_s: float = 300.0,
    kill: bool = True,
) -> PlayerLog:
    fight = FightRef(
        report_code=report_code,
        fight_id=fight_id,
        encounter_id=encounter_id,
        boss_name="Midnight Falls",
        difficulty=difficulty,
        duration_s=duration_s,
        kill=kill,
        partition=partition,
    )
    build = PlayerBuild(
        character_name=player,
        server="Azralon",
        class_name=class_name,
        spec_name=spec_name,
        role="dps",
        item_level=292.0,
        talent_hash=None,
        tier_pieces=4,
        external_buffs=frozenset({10060, 1022}),
        has_augmentation=True,
    )
    return PlayerLog(
        fight=fight,
        build=build,
        dps=150000.0,
        percentile=percentile,
        cast_timeline={104316: (1.0, 30.0, 60.0), 1122: (5.0,)},
        active_time_pct=0.97,
        damage_by_ability={104316: AbilityDamage(104316, 5_000_000.0, 30, 3)},
        uptimes={395152: 0.8, 410089: 0.4},
        resource_waste={"Fragmentos de Alma": 12.0, "Mana": 3.0},
        deaths=1,
        downtime_s=8.5,
        avg_targets_per_cast={104316: 3.0, 1122: 0.0},
    )


def _seed_discovery(store: Store, log: PlayerLog, *, start_time_ms: int, size: int = 20) -> None:
    discovery = DiscoveryStore(store)
    discovery.write_report(
        report_code=log.fight.report_code,
        zone_id=46,
        start_time_ms=start_time_ms,
        end_time_ms=start_time_ms + 1,
    )
    discovery.write_fight_rankings(
        FightRankings(
            fight_id=log.fight.fight_id,
            partition=log.fight.partition,
            encounter_id=log.fight.encounter_id,
            difficulty=log.fight.difficulty,
            size=size,
            kill=log.fight.kill,
            duration_s=log.fight.duration_s,
            dps=(
                DpsRanking(
                    player_name=log.build.character_name,
                    server_name="Azralon",
                    server_region="US",
                    class_name=log.build.class_name,
                    spec_name=log.build.spec_name,
                    amount=log.dps,
                    rank_percent=log.percentile,
                    bracket_data=292.0,
                    total_parses=500,
                ),
            ),
        ),
        report_code=log.fight.report_code,
    )


# -- feature registry ----------------------------------------------------------


def test_registry_names_are_unique() -> None:
    names = feature_names()
    assert len(names) == len(set(names))


def test_exactly_one_target_column() -> None:
    targets = features_with_role(FeatureRole.TARGET)
    assert len(targets) == 1
    assert targets[0].name == "y_rank_percent"


def test_modellable_features_exclude_identity_and_target() -> None:
    modellable = {spec.name for spec in modellable_features()}
    assert "y_rank_percent" not in modellable
    assert "report_code" not in modellable
    assert "player_name" not in modellable
    assert all(spec.role in MODELLABLE_ROLES for spec in modellable_features())


def test_raw_dps_is_never_a_registry_column() -> None:
    """The headline leak: rankPercent IS the percentile of DPS (§9)."""
    names = set(feature_names())
    assert "dps" not in names
    assert "amount" not in names
    assert "bracket_percent" not in names
    assert "total_parses" not in names


def test_leakage_columns_are_declared_explicitly() -> None:
    excluded = {spec.name for spec in EXCLUDED_LEAKAGE_COLUMNS}
    assert {"dps", "amount", "bracket_percent", "total_parses"} <= excluded
    assert all(spec.role is FeatureRole.EXCLUDED_LEAKAGE for spec in EXCLUDED_LEAKAGE_COLUMNS)


def test_leakage_columns_never_overlap_the_registry() -> None:
    assert not (set(feature_names()) & {spec.name for spec in EXCLUDED_LEAKAGE_COLUMNS})


def test_controllable_features_are_prefixed_and_non_empty() -> None:
    names = controllable_feature_names()
    assert names
    assert all(name.startswith("c_") for name in names)


def test_context_and_non_controllable_use_distinct_prefixes() -> None:
    assert all(s.name.startswith("ctx_") for s in features_with_role(FeatureRole.CONTEXT))
    assert all(s.name.startswith("nc_") for s in features_with_role(FeatureRole.NON_CONTROLLABLE))


def test_every_registry_entry_has_a_description() -> None:
    assert all(spec.description for spec in FEATURE_REGISTRY)


def test_alignment_score_is_deliberately_absent() -> None:
    """Cohort-relative and would leak across the split boundary (§9); its
    absence is a documented decision, so lock it in.
    """
    assert not any("alignment" in name for name in feature_names())


# -- build_features -------------------------------------------------------------


def test_build_features_emits_exactly_the_numeric_registry_columns() -> None:
    features = build_features(_player_log(), raid_size=20)
    expected = {
        spec.name
        for spec in FEATURE_REGISTRY
        if spec.role in MODELLABLE_ROLES and not spec.name.startswith(("ctx_class", "ctx_spec"))
    }
    assert set(features) == expected


def test_build_features_computes_rates_and_aggregates() -> None:
    features = build_features(_player_log(), raid_size=20)

    assert features["c_total_casts"] == 4.0
    assert features["c_distinct_abilities_cast"] == 2.0
    assert features["c_casts_per_minute"] == pytest.approx(4.0 / 5.0)
    assert features["c_mean_uptime"] == pytest.approx(0.6)
    assert features["c_n_tracked_auras"] == 2.0
    assert features["c_resource_waste_total"] == 15.0
    assert features["c_resource_waste_per_minute"] == pytest.approx(3.0)
    assert "c_mean_targets_per_cast" not in features
    assert features["nc_n_external_buffs"] == 2.0
    assert features["nc_has_augmentation"] == 1.0
    assert features["nc_raid_size"] == 20.0


def test_build_features_never_emits_localized_resource_labels() -> None:
    """`resource_waste` keys are PT-BR display labels; only the total may
    become a feature (§7 canonical identifiers).
    """
    features = build_features(_player_log(), raid_size=20)
    assert not any("Fragmentos" in name or "Mana" in name for name in features)


def test_build_features_tolerates_a_zero_duration_fight() -> None:
    features = build_features(_player_log(duration_s=0.0), raid_size=None)
    assert features["c_casts_per_minute"] == 0.0
    assert features["c_resource_waste_per_minute"] == 0.0
    assert features["nc_raid_size"] == 0.0


def test_build_features_handles_an_empty_log() -> None:
    bare = PlayerLog(
        fight=_player_log().fight,
        build=_player_log().build,
        dps=None,
        percentile=None,
        cast_timeline={},
    )
    features = build_features(bare, raid_size=None)
    assert features["c_total_casts"] == 0.0
    assert features["c_mean_uptime"] == 0.0
    assert "c_mean_targets_per_cast" not in features


# -- dataset materialization -----------------------------------------------------


def test_build_returns_an_empty_dataset_when_nothing_is_collected(tmp_path: Path) -> None:
    """Doc §16: status/aggregate must work before any Stage C run."""
    with Store(tmp_path) as store:
        DiscoveryStore(store)
        dataset = ExperimentalDatasetBuilder(store).build()

    assert dataset.is_empty
    assert len(dataset) == 0
    assert dataset.temporal_span_ms is None
    assert dataset.specs == frozenset()
    assert dataset.sorted_by_time() == ()


def test_build_materializes_one_row_per_log(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        log = _player_log()
        _seed_discovery(store, log, start_time_ms=1_700_000_000_000)
        store.write_log(log)

        dataset = ExperimentalDatasetBuilder(store).build()

    assert len(dataset) == 1
    observation = dataset.observations[0]
    assert observation.observation_key == ("R0000000000000A", 1, "Zarad")
    assert observation.y_rank_percent == 55.0
    assert observation.bucket == "40-60"
    assert observation.spec_key == "Warlock/Demonology"


def test_observation_preserves_the_phase4_target(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        log = _player_log()
        _seed_discovery(store, log, start_time_ms=1_700_000_000_000)
        store.write_log(log)

        dataset = ExperimentalDatasetBuilder(store).build()

    assert dataset.observations[0].target.target_id == f"Warlock/Demonology/{ENCOUNTER}/5/4"
    assert dataset.targets == {f"Warlock/Demonology/{ENCOUNTER}/5/4"}


def test_observed_at_comes_from_the_fight_not_the_ingestion(tmp_path: Path) -> None:
    """Ordering by `ingested_at` would scramble every temporal protocol —
    it records when WE fetched the log, not when the pull happened.
    """
    with Store(tmp_path) as store:
        log = _player_log()
        _seed_discovery(store, log, start_time_ms=1_700_000_000_000)
        store.write_log(log)

        dataset = ExperimentalDatasetBuilder(store).build()

    assert dataset.observations[0].observed_at_ms == 1_700_000_000_000


def test_dedup_keeps_only_the_latest_ingestion(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        first = _player_log(percentile=20.0)
        _seed_discovery(store, first, start_time_ms=1_700_000_000_000)
        store.write_log(first)
        time.sleep(0.01)
        store.write_log(_player_log(percentile=90.0))  # same natural key

        dataset = ExperimentalDatasetBuilder(store).build()

    assert len(dataset) == 1
    assert dataset.observations[0].y_rank_percent == 90.0


def test_rows_without_a_percentile_are_excluded(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        log = _player_log(percentile=None)
        _seed_discovery(store, log, start_time_ms=1_700_000_000_000)
        store.write_log(log)

        dataset = ExperimentalDatasetBuilder(store).build()

    assert dataset.is_empty


def test_non_kill_rows_are_excluded(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        log = _player_log(kill=False)
        _seed_discovery(store, log, start_time_ms=1_700_000_000_000)
        store.write_log(log)

        dataset = ExperimentalDatasetBuilder(store).build()

    assert dataset.is_empty


def test_rows_without_a_partition_are_excluded(tmp_path: Path) -> None:
    """A NULL partition cannot be assigned to a Phase4Target at all."""
    with Store(tmp_path) as store:
        log = _player_log(partition=None)
        _seed_discovery(store, log, start_time_ms=1_700_000_000_000)
        store.write_log(log)

        dataset = ExperimentalDatasetBuilder(store).build()

    assert dataset.is_empty


def test_partition_and_difficulty_filters_are_honoured(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        keep = _player_log(report_code="RKEEP0000000000", partition=4, difficulty=5)
        drop = _player_log(report_code="RDROP0000000000", partition=2, difficulty=3)
        _seed_discovery(store, keep, start_time_ms=1_700_000_000_000)
        _seed_discovery(store, drop, start_time_ms=1_700_000_100_000)
        store.write_log(keep)
        store.write_log(drop)

        builder = ExperimentalDatasetBuilder(store)
        by_partition = builder.build(partition=4)
        by_difficulty = builder.build(difficulties=frozenset({5}))

    assert {o.report_code for o in by_partition.observations} == {"RKEEP0000000000"}
    assert {o.report_code for o in by_difficulty.observations} == {"RKEEP0000000000"}


def test_dataset_reports_spec_and_encounter_coverage(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        first = _player_log(report_code="RA00000000000AA", encounter_id=3183)
        second = _player_log(
            report_code="RB00000000000BB",
            player="Other",
            class_name="Mage",
            spec_name="Frost",
            encounter_id=3182,
        )
        _seed_discovery(store, first, start_time_ms=1_700_000_000_000)
        _seed_discovery(store, second, start_time_ms=1_700_000_500_000)
        store.write_log(first)
        store.write_log(second)

        dataset = ExperimentalDatasetBuilder(store).build()

    assert dataset.specs == {"Warlock/Demonology", "Mage/Frost"}
    assert dataset.encounters == {3182, 3183}
    assert dataset.temporal_span_ms == (1_700_000_000_000, 1_700_000_500_000)


def test_sorted_by_time_is_total_and_stable(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        # Two players in the same fight share a timestamp: the natural key
        # must break the tie so ordering never depends on query order.
        first = _player_log(player="Aaa")
        second = _player_log(player="Bbb")
        _seed_discovery(store, first, start_time_ms=1_700_000_000_000)
        store.write_log(first)
        store.write_log(second)

        dataset = ExperimentalDatasetBuilder(store).build()

    ordered = dataset.sorted_by_time()
    assert [o.player_name for o in ordered] == ["Aaa", "Bbb"]
    assert dataset.sorted_by_time() == ordered


def test_unreadable_parquet_is_skipped_and_counted(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        log = _player_log()
        _seed_discovery(store, log, start_time_ms=1_700_000_000_000)
        store.write_log(log)
        row = store.query("SELECT parquet_path FROM logs")
        Path(row["parquet_path"][0]).unlink()

        dataset = ExperimentalDatasetBuilder(store).build()

    assert dataset.is_empty
    assert dataset.skipped_incomplete == 1


def test_empty_dataset_helper_is_constructible_directly() -> None:
    dataset = ExperimentalFeatureDataset(observations=())
    assert dataset.is_empty
    assert dataset.encounters == frozenset()
    assert dataset.targets == frozenset()
