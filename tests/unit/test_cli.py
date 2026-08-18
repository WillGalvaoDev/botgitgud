from __future__ import annotations

import pytest

from botgitgud.cli import _cmd_backfill, build_parser


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


@pytest.mark.parametrize("command", ["probe-schema", "backfill"])
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


def test_backfill_stub_returns_failure_and_explains_why(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = _cmd_backfill(build_parser().parse_args(["backfill"]))
    assert exit_code == 1
    assert "D-13" in capsys.readouterr().err
