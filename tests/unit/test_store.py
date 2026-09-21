from __future__ import annotations

import dataclasses
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from botgitgud.domain.models import (
    AbilityDamage,
    AbilitySourceDamage,
    AuraBand,
    AuraDetail,
    FightRef,
    PlayerBuild,
    PlayerLog,
    RankingCandidate,
    RunManifest,
)
from botgitgud.ingest.store import Store


def _fight(report_code: str = "ABCDEFGHIJKLMNOP", fight_id: int = 1) -> FightRef:
    return FightRef(
        report_code=report_code,
        fight_id=fight_id,
        encounter_id=3179,
        boss_name="Fallen-King Salhadaar",
        difficulty=5,
        duration_s=345.1,
        kill=True,
        partition=4,
    )


def _build(character_name: str = "Zarad", spec_name: str = "Demonology") -> PlayerBuild:
    return PlayerBuild(
        character_name=character_name,
        server="Azralon",
        class_name="Warlock",
        spec_name=spec_name,
        role="dps",
        item_level=283.0,
        talent_hash=None,
        tier_pieces=None,
    )


def _log(
    character_name: str = "Zarad", spec_name: str = "Demonology", **overrides: object
) -> PlayerLog:
    defaults: dict[str, object] = {
        "fight": _fight(),
        "build": _build(character_name, spec_name),
        "dps": 108297.0,
        "percentile": 57.0,
        "cast_timeline": {104316: (1.3, 22.2, 43.1)},
        "damage_by_ability": {104316: AbilityDamage(104316, 500_000.0, 17, 17)},
        "uptimes": {395152: 0.42},
        "resource_waste": {"fury": 12.5},
        "deaths": 0,
    }
    defaults.update(overrides)
    return PlayerLog(**defaults)  # type: ignore[arg-type]


# -- documented acceptance criteria ------------------------------------------


def test_write_and_read_50_synthetic_logs_structural_equality(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        logs = [
            _log(character_name=f"Player{i}", cast_timeline={i: (float(i), float(i) + 1)})
            for i in range(50)
        ]
        for log in logs:
            store.write_log(log)

        for i, original in enumerate(logs):
            read_back = store.read_log(
                original.fight.report_code, original.fight.fight_id, f"Player{i}"
            )
            assert read_back == original


def test_query_with_named_parameters(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        store.write_log(_log(character_name="HavocPlayer", spec_name="Havoc"))
        store.write_log(_log(character_name="OtherPlayer", spec_name="Fire"))

        df = store.query("SELECT count(*) AS n FROM logs WHERE spec_name = $spec", spec="Havoc")
        assert df["n"][0] == 1


def test_rewriting_same_log_does_not_erase_previous_read_returns_latest(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        original = _log(dps=100000.0)
        store.write_log(original)
        time.sleep(0.01)  # ensure a strictly later ingested_at millisecond
        updated = _log(dps=200000.0)
        store.write_log(updated)

        # Both ingestions are preserved (immutability) ...
        rows = store.query(
            "SELECT dps FROM logs WHERE report_code = $c AND fight_id = $f AND player_name = $p "
            "ORDER BY ingested_at",
            c=original.fight.report_code,
            f=original.fight.fight_id,
            p=original.build.character_name,
        )
        assert list(rows["dps"]) == [100000.0, 200000.0]

        # ... but a plain read returns the most recent.
        latest = store.read_log(
            original.fight.report_code, original.fight.fight_id, original.build.character_name
        )
        assert latest is not None
        assert latest.dps == 200000.0


# -- additional coverage --------------------------------------------------------


def test_write_log_persists_kill_flag(tmp_path: Path) -> None:
    """T-DG.5: `kill` was already on FightRef/Parquet but not the `logs`
    table — the Data Acquisition Gate's validity contract needs it at SQL
    level.
    """
    with Store(tmp_path) as store:
        store.write_log(_log(fight=dataclasses.replace(_fight(), kill=False)))
        rows = store.query("SELECT kill FROM logs")
    assert rows["kill"][0] is False


def test_has_log_false_before_write_true_after(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        assert store.has_log("ABCDEFGHIJKLMNOP", 1, "Zarad") is False
        store.write_log(_log())
        assert store.has_log("ABCDEFGHIJKLMNOP", 1, "Zarad") is True


def test_read_log_returns_none_when_absent(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        assert store.read_log("NOPE0000000000AA", 1, "Nobody") is None


def test_read_log_preserves_nested_structures_exactly(tmp_path: Path) -> None:
    log = _log(
        cast_timeline={1: (1.0, 2.5, 3.75), 2: (10.0,)},
        damage_by_ability={
            1: AbilityDamage(1, 100.0, 5, 5),
            2: AbilityDamage(2, 200.0, 3, 4),
        },
        uptimes={1: 0.9, 2: 0.1},
        resource_waste={"fury": 5.0, "mana": 0.0},
        deaths=2,
    )
    with Store(tmp_path) as store:
        store.write_log(log)
        read_back = store.read_log("ABCDEFGHIJKLMNOP", 1, "Zarad")

    assert read_back is not None
    assert read_back.cast_timeline == log.cast_timeline
    assert read_back.damage_by_ability == log.damage_by_ability
    assert read_back.uptimes == log.uptimes
    assert read_back.resource_waste == log.resource_waste
    assert read_back.deaths == 2


def test_round_trip_preserves_m6_signals(tmp_path: Path) -> None:
    log = _log(
        damage_by_ability={
            1: AbilityDamage(
                1,
                100.0,
                3,
                2,
                by_source={
                    6: AbilitySourceDamage(6, 70.0, 2),
                    16: AbilitySourceDamage(16, 30.0, 1),
                },
            )
        },
        resource_waste_by_ability={7: {10: 3.0, 11: 2.0}},
        aura_details={5: AuraDetail(12, (AuraBand(200, 300), AuraBand(10, 20)))},
    )
    with Store(tmp_path) as store:
        store.write_log(log)
        read_back = store.read_log("ABCDEFGHIJKLMNOP", 1, "Zarad")
    assert read_back == log


def test_partition_none_falls_back_to_unknown_directory(tmp_path: Path) -> None:
    fight = FightRef(
        report_code="NOPARTITION00001",
        fight_id=1,
        encounter_id=1,
        boss_name="Test",
        difficulty=1,
        duration_s=100.0,
        kill=True,
        partition=None,
    )
    log = PlayerLog(fight=fight, build=_build(), dps=1.0, percentile=1.0, cast_timeline={})
    with Store(tmp_path) as store:
        store.write_log(log)
        read_back = store.read_log("NOPARTITION00001", 1, "Zarad")
    assert read_back is not None
    assert read_back.fight.partition is None
    assert (tmp_path / "raw" / "encounter_id=1" / "difficulty=1" / "partition=unknown").exists()


def _candidate(
    report_code: str = "ABCDEFGHIJKLMNOP",
    fight_id: int = 1,
    player_name: str = "Ref0",
    duration_s: float = 300.0,
) -> RankingCandidate:
    return RankingCandidate(
        report_code=report_code, fight_id=fight_id, player_name=player_name, duration_s=duration_s
    )


def test_read_candidate_pool_none_before_any_write(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        assert store.read_candidate_pool("deadbeefdeadbeef") is None


def test_candidate_pool_round_trip(tmp_path: Path) -> None:
    candidates = [_candidate(player_name=f"Ref{i}", duration_s=300.0 + i) for i in range(5)]
    with Store(tmp_path) as store:
        store.write_candidate_pool("deadbeefdeadbeef", candidates)
        read_back = store.read_candidate_pool("deadbeefdeadbeef")

    assert read_back is not None
    assert len(read_back) == 5
    assert {c.player_name for c in read_back} == {f"Ref{i}" for i in range(5)}


def test_write_candidate_pool_replaces_the_previous_set_for_the_same_cohort_id(
    tmp_path: Path,
) -> None:
    with Store(tmp_path) as store:
        store.write_candidate_pool("samehash00000001", [_candidate(player_name="Old")])
        store.write_candidate_pool("samehash00000001", [_candidate(player_name="New")])

        read_back = store.read_candidate_pool("samehash00000001")

    assert read_back is not None
    assert len(read_back) == 1  # replaced, not accumulated
    assert read_back[0].player_name == "New"


def test_write_run_persists_manifest_row(tmp_path: Path) -> None:
    manifest = RunManifest(
        cohort_id="deadbeefdeadbeef",
        code_version="abc1234",
        generated_at=datetime.now(UTC),
        n_members=17,
        wcl_partition=4,
        settings_hash="feedface1234",
    )
    with Store(tmp_path) as store:
        store.write_run(manifest)
        rows = store.query(
            "SELECT cohort_id, code_version, n_members, wcl_partition, settings_hash FROM runs"
        )
    assert rows["cohort_id"][0] == "deadbeefdeadbeef"
    assert rows["code_version"][0] == "abc1234"
    assert rows["n_members"][0] == 17
    assert rows["wcl_partition"][0] == 4
    assert rows["settings_hash"][0] == "feedface1234"


def test_write_run_twice_keeps_both_rows(tmp_path: Path) -> None:
    """Insert-only audit log (D-12c immutability rationale): repeated runs
    of the same cohort must not overwrite each other.
    """
    manifest = RunManifest(
        cohort_id="samecohort000001",
        code_version="abc1234",
        generated_at=datetime.now(UTC),
        n_members=17,
        wcl_partition=4,
        settings_hash="feedface1234",
    )
    with Store(tmp_path) as store:
        store.write_run(manifest)
        store.write_run(manifest)
        rows = store.query(
            "SELECT count(*) AS n FROM runs WHERE cohort_id = $c", c="samecohort000001"
        )
    assert rows["n"][0] == 2


def test_reopening_store_reuses_existing_tables(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        store.write_log(_log())

    with Store(tmp_path) as store:
        assert store.has_log("ABCDEFGHIJKLMNOP", 1, "Zarad") is True


# -- T1.8: concurrent write safety ------------------------------------------------


def test_eight_threads_writing_concurrently_never_raises_and_all_records_land(
    tmp_path: Path,
) -> None:
    """T1.8's own acceptance criterion: 8 workers writing simultaneously ->
    no DuckDB concurrent-access exception, every record present. The
    single-connection Store is only safe under concurrency because every
    access goes through self._lock (see module docstring).
    """
    n_threads = 8
    errors: list[BaseException] = []
    barrier = threading.Barrier(n_threads)

    def _writer(i: int) -> None:
        try:
            barrier.wait(timeout=5)  # maximize actual overlap
            store.write_log(_log(character_name=f"Concurrent{i}"))
        except BaseException as exc:  # captured for the main thread to assert on
            errors.append(exc)

    with Store(tmp_path) as store:
        threads = [threading.Thread(target=_writer, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == []
        rows = store.query("SELECT count(*) AS n FROM logs")
        assert rows["n"][0] == n_threads
        for i in range(n_threads):
            assert store.has_log("ABCDEFGHIJKLMNOP", 1, f"Concurrent{i}") is True
