from __future__ import annotations

import argparse

import pytest

from botgitgud.cli import build_parser
from botgitgud.cli_experiment import _fmt_span, _parse_spec, _print_campaign, _print_dataset
from botgitgud.domain.specs import SpecId
from botgitgud.phase4.experiment import ExperimentBudget
from botgitgud.phase4.experiment_campaign import (
    ExperimentCampaign,
    PlannedExperimentObservation,
    StatisticalExperimentPlan,
)
from botgitgud.phase4.experimental_dataset import ExperimentalFeatureDataset

# -- argument parsing -----------------------------------------------------------


def test_experiment_plan_parses_required_flags() -> None:
    args = build_parser().parse_args(["experiment-plan", "--partition", "4", "--difficulty", "5"])
    assert args.command == "experiment-plan"
    assert args.partition == 4
    assert args.difficulty == [5]
    assert args.max_observations == 1200
    assert args.max_points == 25_000.0
    assert args.spec is None
    assert args.encounter is None


def test_experiment_plan_accepts_several_difficulties_and_filters() -> None:
    args = build_parser().parse_args(
        [
            "experiment-plan",
            "--partition",
            "4",
            "--difficulty",
            "4",
            "5",
            "--spec",
            "Mage/Frost",
            "--spec",
            "Priest/Shadow",
            "--encounter",
            "3182",
            "--max-points",
            "10000",
        ]
    )
    assert args.difficulty == [4, 5]
    assert args.spec == [SpecId("Mage", "Frost"), SpecId("Priest", "Shadow")]
    assert args.encounter == [3182]
    assert args.max_points == 10_000.0


def test_experiment_plan_requires_partition_and_difficulty() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["experiment-plan", "--partition", "4"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["experiment-plan", "--difficulty", "5"])


def test_experiment_status_needs_no_flags() -> None:
    args = build_parser().parse_args(["experiment-status"])
    assert args.command == "experiment-status"
    assert args.partition is None
    assert args.difficulty is None
    assert args.func is not None


def test_experiment_collect_parses_safe_campaign_arguments() -> None:
    args = build_parser().parse_args(
        [
            "experiment-collect",
            "--partition",
            "4",
            "--difficulty",
            "5",
            "--max-observations",
            "1200",
            "--max-api-points",
            "8040",
            "--dry-run",
        ]
    )
    assert args.partition == 4
    assert args.difficulty == [5]
    assert args.max_observations == 1200
    assert args.max_api_points == 8040
    assert args.dry_run is True


def test_experiment_collect_resume_uses_campaign_id() -> None:
    args = build_parser().parse_args(["experiment-collect", "--campaign", "exp-abc"])
    assert args.campaign == "exp-abc"
    assert args.partition is None


def test_parse_spec_accepts_class_slash_spec() -> None:
    assert _parse_spec("DeathKnight/Unholy") == SpecId("DeathKnight", "Unholy")
    assert _parse_spec(" Mage / Frost ") == SpecId("Mage", "Frost")


def test_parse_spec_rejects_a_missing_separator() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="Class/Spec"):
        _parse_spec("Unholy")


# -- rendering -------------------------------------------------------------------


def _observation(
    *, spec_name: str = "Frost", encounter_id: int = 3176, rank: float = 50.0, player: str = "P1"
) -> PlannedExperimentObservation:
    return PlannedExperimentObservation(
        report_code="R1",
        fight_id=1,
        player_name=player,
        class_name="Mage",
        spec_name=spec_name,
        encounter_id=encounter_id,
        difficulty=5,
        partition=4,
        rank_percent=rank,
        bucket="40-60",
        start_time_ms=1_700_000_000_000,
    )


def _campaign(observations: tuple[PlannedExperimentObservation, ...]) -> ExperimentCampaign:
    request = StatisticalExperimentPlan(
        partition=4,
        difficulties=frozenset({5}),
        budget=ExperimentBudget(max_api_points=25_000.0),
        max_observations=1200,
    )
    return ExperimentCampaign(
        request=request,
        observations=observations,
        candidates_available=7333,
        strata_total=833,
        strata_covered=len(observations),
        estimated_api_points=17.0 * len(observations),
        stopped_reason="max_observations",
    )


def test_print_campaign_reports_every_required_section(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Task §14: available, planned, targets, specs, encounters, buckets,
    temporal span, Stage C cost and remaining budget.
    """
    _print_campaign(_campaign((_observation(), _observation(player="P2"))))
    out = capsys.readouterr().out

    assert "observacoes candidatas" in out
    assert "7333" in out
    assert "Phase4Targets envolvidos" in out
    assert "Distribuicao por faixa de rankPercent" in out
    assert "Distribuicao por spec" in out
    assert "Distribuicao por encounter" in out
    assert "cobertura temporal" in out
    assert "custo estimado do Stage C" in out
    assert "restante sob o teto" in out


def test_print_campaign_lists_every_percentile_bucket_even_at_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A starved band must be visible as 0, not silently missing."""
    _print_campaign(_campaign((_observation(),)))
    out = capsys.readouterr().out

    for bucket in ("00-20", "20-40", "40-60", "60-80", "80-100"):
        assert bucket in out


def test_print_campaign_states_that_nothing_was_collected(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _print_campaign(_campaign((_observation(),)))
    assert "NENHUMA COLETA FOI EXECUTADA" in capsys.readouterr().out


def test_print_campaign_handles_an_empty_campaign(capsys: pytest.CaptureFixture[str]) -> None:
    _print_campaign(_campaign(()))
    out = capsys.readouterr().out
    assert "observacoes ...................... 0" in out
    assert "custo por observacao" not in out  # no division by zero


def test_print_campaign_flags_insufficient_coverage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _print_campaign(_campaign((_observation(),)))
    assert "INSUFICIENTE" in capsys.readouterr().out


def test_print_dataset_works_with_nothing_collected(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Task §16: status must work before any Stage C run."""
    _print_dataset(ExperimentalFeatureDataset(observations=()))
    out = capsys.readouterr().out

    assert "observacoes coletadas ............... 0" in out
    assert "Nenhuma observacao experimental coletada ainda" in out


def test_fmt_span_handles_absent_data() -> None:
    assert _fmt_span(None) == "n/d"


def test_fmt_span_reports_days() -> None:
    rendered = _fmt_span((1_700_000_000_000, 1_700_086_400_000))
    assert "1.00 dias" in rendered
