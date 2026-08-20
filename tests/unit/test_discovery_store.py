from __future__ import annotations

from pathlib import Path

from botgitgud.domain.models import FightRef, PlayerBuild, PlayerLog
from botgitgud.ingest.discovery_store import DiscoveryStore
from botgitgud.ingest.fight_rankings import DpsRanking, FightRankings
from botgitgud.ingest.store import Store


def _fight_rankings(
    *,
    fight_id: int = 21,
    partition: int | None = 3,
    dps: tuple[DpsRanking, ...] = (),
) -> FightRankings:
    return FightRankings(
        fight_id=fight_id,
        partition=partition,
        encounter_id=3179,
        difficulty=5,
        size=20,
        kill=True,
        duration_s=229.637,
        dps=dps,
    )


def _dps_ranking(name: str = "Kilama", rank_percent: float | None = 48) -> DpsRanking:
    return DpsRanking(
        player_name=name,
        server_name="Kazzak",
        server_region="EU",
        class_name="Druid",
        spec_name="Balance",
        amount=122010.2,
        rank_percent=rank_percent,
        bracket_data=291,
        total_parses=6410,
    )


# -- discovery_reports (Estágio A) -------------------------------------------


def test_has_report_false_before_write_true_after(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        assert discovery.has_report("ABCDEFGHIJKLMNOP") is False
        discovery.write_report(
            report_code="ABCDEFGHIJKLMNOP", zone_id=46, start_time_ms=1000, end_time_ms=2000
        )
        assert discovery.has_report("ABCDEFGHIJKLMNOP") is True


def test_writing_the_same_report_twice_does_not_duplicate(tmp_path: Path) -> None:
    """T-DG.2 acceptance criterion: inserting the same key twice upserts,
    never duplicates.
    """
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_report(
            report_code="ABCDEFGHIJKLMNOP", zone_id=46, start_time_ms=1000, end_time_ms=2000
        )
        discovery.write_report(
            report_code="ABCDEFGHIJKLMNOP", zone_id=46, start_time_ms=1000, end_time_ms=2000
        )
        assert discovery.count_reports() == 1


def test_count_reports_filters_by_zone(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_report(report_code="A", zone_id=46, start_time_ms=0, end_time_ms=1)
        discovery.write_report(report_code="B", zone_id=46, start_time_ms=0, end_time_ms=1)
        discovery.write_report(report_code="C", zone_id=99, start_time_ms=0, end_time_ms=1)

        assert discovery.count_reports(zone_id=46) == 2
        assert discovery.count_reports(zone_id=99) == 1
        assert discovery.count_reports() == 3


# -- discovery_fights + discovery_targets (Estágio B) ------------------------


def test_has_fight_false_before_write_true_after(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        assert discovery.has_fight("ABCDEFGHIJKLMNOP", 21) is False
        discovery.write_fight_rankings(_fight_rankings(), report_code="ABCDEFGHIJKLMNOP")
        assert discovery.has_fight("ABCDEFGHIJKLMNOP", 21) is True


def test_write_fight_rankings_persists_fight_level_fields(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_fight_rankings(_fight_rankings(partition=3), report_code="ABCDEFGHIJKLMNOP")

        df = store.query(
            "SELECT partition, encounter_id, difficulty, kill, duration_s "
            "FROM discovery_fights WHERE report_code = $c AND fight_id = $f",
            c="ABCDEFGHIJKLMNOP",
            f=21,
        )
        assert df["partition"][0] == 3
        assert df["encounter_id"][0] == 3179
        assert df["difficulty"][0] == 5
        assert df["kill"][0] is True
        assert df["duration_s"][0] == 229.637


def test_write_fight_rankings_persists_every_dps_character(tmp_path: Path) -> None:
    dps = (_dps_ranking("A", 48), _dps_ranking("B", 88))
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_fight_rankings(_fight_rankings(dps=dps), report_code="ABCDEFGHIJKLMNOP")

        df = store.query(
            "SELECT player_name, rank_percent FROM discovery_targets "
            "WHERE report_code = $c AND fight_id = $f ORDER BY player_name",
            c="ABCDEFGHIJKLMNOP",
            f=21,
        )
        assert list(df["player_name"]) == ["A", "B"]
        assert list(df["rank_percent"]) == [48, 88]


def test_writing_the_same_fight_twice_does_not_duplicate(tmp_path: Path) -> None:
    dps = (_dps_ranking("A"),)
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_fight_rankings(_fight_rankings(dps=dps), report_code="ABCDEFGHIJKLMNOP")
        discovery.write_fight_rankings(_fight_rankings(dps=dps), report_code="ABCDEFGHIJKLMNOP")

        fights = store.query("SELECT count(*) AS n FROM discovery_fights")
        targets = store.query("SELECT count(*) AS n FROM discovery_targets")
        assert fights["n"][0] == 1
        assert targets["n"][0] == 1


def test_write_fight_rankings_with_no_dps_writes_only_the_fight_row(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_fight_rankings(_fight_rankings(dps=()), report_code="ABCDEFGHIJKLMNOP")

        assert discovery.has_fight("ABCDEFGHIJKLMNOP", 21) is True
        targets = store.query("SELECT count(*) AS n FROM discovery_targets")
        assert targets["n"][0] == 0


# -- checkpoints ----------------------------------------------------------------


def test_read_checkpoint_none_before_any_write(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        assert discovery.read_checkpoint("discover:zone=46", 1000, 2000) is None


def test_write_and_read_checkpoint_round_trip(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_checkpoint(
            job_key="discover:zone=46",
            zone_id=46,
            window_start=1000,
            window_end=2000,
            last_page=3,
            state="in_progress",
            points_spent=27.0,
        )
        checkpoint = discovery.read_checkpoint("discover:zone=46", 1000, 2000)

    assert checkpoint is not None
    assert checkpoint.job_key == "discover:zone=46"
    assert checkpoint.zone_id == 46
    assert checkpoint.last_page == 3
    assert checkpoint.state == "in_progress"
    assert checkpoint.points_spent == 27.0


def test_writing_the_same_checkpoint_twice_updates_not_duplicates(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_checkpoint(
            job_key="discover:zone=46",
            zone_id=46,
            window_start=1000,
            window_end=2000,
            last_page=1,
            state="in_progress",
            points_spent=9.0,
        )
        discovery.write_checkpoint(
            job_key="discover:zone=46",
            zone_id=46,
            window_start=1000,
            window_end=2000,
            last_page=5,
            state="done",
            points_spent=45.0,
        )

        checkpoints = discovery.list_checkpoints("discover:zone=46")

    assert len(checkpoints) == 1
    assert checkpoints[0].last_page == 5
    assert checkpoints[0].state == "done"


def test_list_checkpoints_ordered_by_window_start(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_checkpoint(
            job_key="discover:zone=46",
            zone_id=46,
            window_start=3000,
            window_end=4000,
            last_page=0,
            state="pending",
            points_spent=0.0,
        )
        discovery.write_checkpoint(
            job_key="discover:zone=46",
            zone_id=46,
            window_start=1000,
            window_end=2000,
            last_page=0,
            state="pending",
            points_spent=0.0,
        )

        checkpoints = discovery.list_checkpoints("discover:zone=46")

    assert [c.window_start for c in checkpoints] == [1000, 3000]


def test_list_checkpoints_scoped_to_job_key(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_checkpoint(
            job_key="discover:zone=46",
            zone_id=46,
            window_start=1000,
            window_end=2000,
            last_page=0,
            state="pending",
            points_spent=0.0,
        )
        discovery.write_checkpoint(
            job_key="discover:zone=99",
            zone_id=99,
            window_start=1000,
            window_end=2000,
            last_page=0,
            state="pending",
            points_spent=0.0,
        )

        assert len(discovery.list_checkpoints("discover:zone=46")) == 1
        assert len(discovery.list_checkpoints("discover:zone=99")) == 1


# -- shares Store's lock/connection (T1.8, D-19) -----------------------------


def test_discovery_store_reuses_the_same_underlying_store(tmp_path: Path) -> None:
    """No separate DuckDB connection is opened — table creation and every
    write go through Store's own execute()/execute_returning(), which
    serialize on Store._lock (module docstring, D-19).
    """
    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        discovery.write_report(report_code="X", zone_id=1, start_time_ms=0, end_time_ms=1)

    with Store(tmp_path) as store:
        discovery = DiscoveryStore(store)
        assert discovery.has_report("X") is True


# -- regression: logs table keeps its no-PK immutability contract (D-12c) ----


def test_logs_table_still_has_no_primary_key_and_allows_duplicate_inserts(
    tmp_path: Path,
) -> None:
    """T-DG.2 must not touch ingest/store.py's own tables — `logs` keeps its
    deliberately-absent PRIMARY KEY (D-12c: a second insert for the same
    (report_code, fight_id, player_name) must SUCCEED, not raise).
    """
    fight = FightRef(
        report_code="ABCDEFGHIJKLMNOP",
        fight_id=1,
        encounter_id=3179,
        boss_name="Fallen-King Salhadaar",
        difficulty=5,
        duration_s=345.1,
        kill=True,
        partition=3,
    )
    build = PlayerBuild(
        character_name="Zarad",
        server="Azralon",
        class_name="Warlock",
        spec_name="Demonology",
        role="dps",
        item_level=283.0,
        talent_hash=None,
        tier_pieces=None,
    )
    log = PlayerLog(fight=fight, build=build, dps=100.0, percentile=50.0, cast_timeline={})

    with Store(tmp_path) as store:
        DiscoveryStore(store)  # creates discovery_* tables alongside logs
        store.write_log(log)
        store.write_log(log)  # must not raise

        rows = store.query("SELECT count(*) AS n FROM logs")
    assert rows["n"][0] == 2
