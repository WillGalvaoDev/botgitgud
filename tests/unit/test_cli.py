from __future__ import annotations

import pytest

from botgitgud.cli import build_parser


def test_analyze_subcommand_parses_required_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["analyze", "--report", "ABCDEFGHIJKLMNOP", "--fight", "1", "--char", "Zarad"]
    )
    assert args.command == "analyze"
    assert args.report == "ABCDEFGHIJKLMNOP"
    assert args.fight == 1
    assert args.char == "Zarad"
    assert args.func is not None


def test_analyze_missing_required_flag_exits_nonzero() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["analyze", "--report", "ABCDEFGHIJKLMNOP"])


@pytest.mark.parametrize("command", ["probe-schema", "serve"])
def test_every_no_arg_subcommand_exists(command: str) -> None:
    parser = build_parser()
    args = parser.parse_args([command])
    assert args.command == command
    assert args.func is not None


def test_build_cohort_subcommand_parses_required_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "build-cohort",
            "--encounter",
            "3179",
            "--class",
            "Warlock",
            "--spec",
            "Demonology",
            "--difficulty",
            "5",
        ]
    )
    assert args.command == "build-cohort"
    assert args.encounter == 3179
    assert args.klass == "Warlock"
    assert args.spec == "Demonology"
    assert args.difficulty == 5
    assert args.duration_bucket is None
    assert args.func is not None


def test_build_cohort_accepts_optional_duration_bucket() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "build-cohort",
            "--encounter",
            "3179",
            "--class",
            "Warlock",
            "--spec",
            "Demonology",
            "--difficulty",
            "5",
            "--duration-bucket",
            "345.1",
        ]
    )
    assert args.duration_bucket == pytest.approx(345.1)


def test_build_cohort_missing_required_flag_exits_nonzero() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["build-cohort", "--encounter", "3179"])


def test_no_subcommand_exits_nonzero() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_backfill_is_not_a_public_command() -> None:
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["backfill"])
    assert exc.value.code == 2


# -- T-DG.3: discover subcommand ----------------------------------------------


def test_discover_subcommand_parses_required_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(["discover", "--zone", "46", "--start-ms", "1000", "--end-ms", "2000"])
    assert args.command == "discover"
    assert args.zone == 46
    assert args.start_ms == 1000
    assert args.end_ms == 2000
    assert args.window_hours == 12.0  # default
    assert args.max_points is None  # default
    assert args.func is not None


def test_discover_subcommand_accepts_optional_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "discover",
            "--zone",
            "46",
            "--start-ms",
            "1000",
            "--end-ms",
            "2000",
            "--window-hours",
            "6",
            "--max-points",
            "900",
        ]
    )
    assert args.window_hours == 6.0
    assert args.max_points == 900.0


def test_discover_missing_required_flag_exits_nonzero() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["discover", "--zone", "46"])


def test_triage_subcommand_all_flags_optional() -> None:
    parser = build_parser()
    args = parser.parse_args(["triage"])
    assert args.command == "triage"
    assert args.zone is None
    assert args.max_points is None
    assert args.func is not None


def test_triage_subcommand_accepts_optional_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(["triage", "--zone", "46", "--max-points", "500"])
    assert args.zone == 46
    assert args.max_points == 500.0


def test_dataset_status_subcommand_all_flags_optional() -> None:
    parser = build_parser()
    args = parser.parse_args(["dataset-status"])
    assert args.command == "dataset-status"
    assert args.klass is None
    assert args.spec is None
    assert args.encounter is None
    assert args.difficulty is None
    assert args.partition is None
    assert args.limit == 20
    assert args.func is not None


def test_dataset_status_subcommand_accepts_target_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "dataset-status",
            "--class",
            "DeathKnight",
            "--spec",
            "Unholy",
            "--encounter",
            "3182",
            "--difficulty",
            "5",
            "--partition",
            "3",
        ]
    )
    assert args.klass == "DeathKnight"
    assert args.spec == "Unholy"
    assert args.encounter == 3182
    assert args.difficulty == 5
    assert args.partition == 3
