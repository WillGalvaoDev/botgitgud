from __future__ import annotations

from collections.abc import Callable

import pytest

from botgitgud.domain.specs import SpecId
from botgitgud.phase4.experiment import SplitProtocol
from botgitgud.phase4.experiment_splits import (
    DataSplit,
    SplitInvariantError,
    held_out_encounter_split,
    held_out_logs_temporal_split,
    held_out_spec_encounter_split,
    held_out_spec_split,
    spec_and_encounter_seen_separately,
    temporal_within_target_split,
)
from botgitgud.phase4.experimental_dataset import (
    ExperimentalFeatureDataset,
    ExperimentalObservation,
)
from botgitgud.phase4.target import Phase4Target

DIFFICULTY = 5
PARTITION = 4

SplitFactory = Callable[[ExperimentalFeatureDataset], DataSplit]


def _obs(
    *,
    report_code: str = "R1",
    fight_id: int = 1,
    player: str = "P1",
    at_ms: int = 1_000,
    class_name: str = "Mage",
    spec_name: str = "Frost",
    encounter_id: int = 3176,
    rank_percent: float = 50.0,
) -> ExperimentalObservation:
    return ExperimentalObservation(
        report_code=report_code,
        fight_id=fight_id,
        player_name=player,
        observed_at_ms=at_ms,
        target=Phase4Target(SpecId(class_name, spec_name), encounter_id, DIFFICULTY, PARTITION),
        y_rank_percent=rank_percent,
        features={"c_active_time_pct": 0.9},
    )


def _dataset(observations: list[ExperimentalObservation]) -> ExperimentalFeatureDataset:
    return ExperimentalFeatureDataset(observations=tuple(observations))


def _wide_dataset() -> ExperimentalFeatureDataset:
    """3 specs x 3 encounters x 4 timestamps, one report+player each — wide
    enough for every protocol.
    """
    specs = [("Mage", "Frost"), ("Priest", "Shadow"), ("Warlock", "Demonology")]
    encounters = [3176, 3177, 3178]
    observations = []
    n = 0
    for at in range(4):
        for enc in encounters:
            for class_name, spec_name in specs:
                observations.append(
                    _obs(
                        report_code=f"R{n:04d}",
                        player=f"P{n:04d}",
                        at_ms=1_000 + at * 1_000,
                        class_name=class_name,
                        spec_name=spec_name,
                        encounter_id=enc,
                    )
                )
                n += 1
    return _dataset(observations)


# -- S1: temporal within target -------------------------------------------------


def test_s1_trains_on_the_past_and_validates_on_the_future() -> None:
    dataset = _dataset(
        [_obs(report_code=f"R{i}", player=f"P{i}", at_ms=i * 100) for i in range(10)]
    )
    split = temporal_within_target_split(dataset, validation_fraction=0.3)

    assert split.protocol is SplitProtocol.S1_TEMPORAL_WITHIN_TARGET
    assert split.n_train == 7
    assert split.n_validation == 3
    assert max(o.observed_at_ms for o in split.train) < min(
        o.observed_at_ms for o in split.validation
    )


def test_s1_never_puts_an_observation_on_both_sides() -> None:
    dataset = _dataset(
        [_obs(report_code=f"R{i}", player=f"P{i}", at_ms=i * 100) for i in range(10)]
    )
    split = temporal_within_target_split(dataset)

    train_keys = {o.observation_key for o in split.train}
    validation_keys = {o.observation_key for o in split.validation}
    assert not (train_keys & validation_keys)


def test_s1_does_not_straddle_a_shared_timestamp() -> None:
    """Rows recorded at the same instant must all land on one side, or the
    ordering invariant is violated.
    """
    observations = [_obs(report_code=f"R{i}", player=f"P{i}", at_ms=100) for i in range(4)]
    observations += [_obs(report_code=f"S{i}", player=f"Q{i}", at_ms=200) for i in range(4)]
    split = temporal_within_target_split(_dataset(observations), validation_fraction=0.25)

    assert {o.observed_at_ms for o in split.train}.isdisjoint(
        {o.observed_at_ms for o in split.validation}
    )


def test_s1_rejects_a_degenerate_validation_fraction() -> None:
    dataset = _dataset([_obs()])
    with pytest.raises(ValueError, match="validation_fraction"):
        temporal_within_target_split(dataset, validation_fraction=0.0)
    with pytest.raises(ValueError, match="validation_fraction"):
        temporal_within_target_split(dataset, validation_fraction=1.0)


def test_s1_on_an_empty_dataset_is_not_usable() -> None:
    split = temporal_within_target_split(_dataset([]))
    assert not split.is_usable


# -- S2: held-out logs and players, temporal ------------------------------------


def test_s2_shares_no_report_and_no_player() -> None:
    split = held_out_logs_temporal_split(_wide_dataset(), validation_fraction=0.3)

    assert {o.report_code for o in split.train}.isdisjoint(
        {o.report_code for o in split.validation}
    )
    assert {o.player_name for o in split.train}.isdisjoint(
        {o.player_name for o in split.validation}
    )


def test_s2_still_respects_temporal_order() -> None:
    split = held_out_logs_temporal_split(_wide_dataset(), validation_fraction=0.3)
    assert split.is_usable
    assert max(o.observed_at_ms for o in split.train) < min(
        o.observed_at_ms for o in split.validation
    )


def test_s2_drops_a_returning_player_from_validation() -> None:
    """The same character appearing later must not be scored: that would
    reward memorizing the individual rather than the features.
    """
    observations = [
        _obs(report_code="R1", player="Repeat", at_ms=100),
        _obs(report_code="R2", player="Other", at_ms=200),
        _obs(report_code="R3", player="Repeat", at_ms=300),
        _obs(report_code="R4", player="Fresh", at_ms=400),
    ]
    split = held_out_logs_temporal_split(_dataset(observations), validation_fraction=0.5)

    assert "Repeat" in {o.player_name for o in split.train}
    assert "Repeat" not in {o.player_name for o in split.validation}
    assert "Fresh" in {o.player_name for o in split.validation}


def test_s2_drops_a_second_player_from_a_training_report() -> None:
    observations = [
        _obs(report_code="RSHARED", fight_id=1, player="A", at_ms=100),
        _obs(report_code="ROTHER", fight_id=1, player="B", at_ms=200),
        _obs(report_code="RSHARED", fight_id=2, player="C", at_ms=300),
        _obs(report_code="RFRESH", fight_id=1, player="D", at_ms=400),
    ]
    split = held_out_logs_temporal_split(_dataset(observations), validation_fraction=0.5)

    assert "RSHARED" not in {o.report_code for o in split.validation}
    assert "RFRESH" in {o.report_code for o in split.validation}


# -- S3: held-out encounter -----------------------------------------------------


def test_s3_holds_the_encounter_completely_out_of_training() -> None:
    split = held_out_encounter_split(_wide_dataset(), encounter_id=3177)

    assert split.protocol is SplitProtocol.S3_HELD_OUT_ENCOUNTER
    assert split.held_out == "3177"
    assert 3177 not in {o.target.encounter_id for o in split.train}
    assert {o.target.encounter_id for o in split.validation} == {3177}
    assert split.is_usable


def test_s3_keeps_the_other_encounters_in_training() -> None:
    split = held_out_encounter_split(_wide_dataset(), encounter_id=3177)
    assert {o.target.encounter_id for o in split.train} == {3176, 3178}


def test_s3_validation_targets_are_unseen() -> None:
    split = held_out_encounter_split(_wide_dataset(), encounter_id=3177)
    assert split.unseen_validation_targets == split.validation_targets


def test_s3_with_a_temporal_cutoff_never_trains_on_the_future() -> None:
    split = held_out_encounter_split(_wide_dataset(), encounter_id=3177, temporal_cutoff_ms=3_000)

    assert split.is_usable
    assert max(o.observed_at_ms for o in split.train) < 3_000
    assert min(o.observed_at_ms for o in split.validation) >= 3_000


def test_s3_for_an_absent_encounter_yields_an_unusable_split() -> None:
    split = held_out_encounter_split(_wide_dataset(), encounter_id=9999)
    assert not split.is_usable
    assert split.n_validation == 0


# -- S4: held-out spec ----------------------------------------------------------


def test_s4_holds_the_spec_completely_out_of_training() -> None:
    split = held_out_spec_split(_wide_dataset(), spec_key="Priest/Shadow")

    assert split.protocol is SplitProtocol.S4_HELD_OUT_SPEC
    assert "Priest/Shadow" not in {o.spec_key for o in split.train}
    assert {o.spec_key for o in split.validation} == {"Priest/Shadow"}
    assert split.is_usable


def test_s4_keeps_the_other_specs_in_training() -> None:
    split = held_out_spec_split(_wide_dataset(), spec_key="Priest/Shadow")
    assert {o.spec_key for o in split.train} == {"Mage/Frost", "Warlock/Demonology"}


def test_s4_with_a_temporal_cutoff_never_trains_on_the_future() -> None:
    split = held_out_spec_split(_wide_dataset(), spec_key="Priest/Shadow", temporal_cutoff_ms=3_000)

    assert split.is_usable
    assert max(o.observed_at_ms for o in split.train) < 3_000
    assert min(o.observed_at_ms for o in split.validation) >= 3_000


def test_s4_for_an_absent_spec_yields_an_unusable_split() -> None:
    split = held_out_spec_split(_wide_dataset(), spec_key="Rogue/Outlaw")
    assert not split.is_usable


# -- S5: held-out spec x encounter combination -----------------------------------


def test_s5_holds_out_only_the_combination() -> None:
    split = held_out_spec_encounter_split(_wide_dataset(), spec_key="Mage/Frost", encounter_id=3178)

    assert split.protocol is SplitProtocol.S5_HELD_OUT_SPEC_ENCOUNTER
    assert split.held_out == "Mage/Frost@3178"
    assert not any(
        o.spec_key == "Mage/Frost" and o.target.encounter_id == 3178 for o in split.train
    )
    assert all(
        o.spec_key == "Mage/Frost" and o.target.encounter_id == 3178 for o in split.validation
    )


def test_s5_still_sees_the_spec_and_the_encounter_separately() -> None:
    """Otherwise S5 degenerates into S3 or S4 and says nothing about the
    interaction.
    """
    split = held_out_spec_encounter_split(_wide_dataset(), spec_key="Mage/Frost", encounter_id=3178)

    assert spec_and_encounter_seen_separately(split, spec_key="Mage/Frost", encounter_id=3178)
    assert any(o.spec_key == "Mage/Frost" for o in split.train)
    assert any(o.target.encounter_id == 3178 for o in split.train)


def test_s5_precondition_is_false_when_the_spec_is_absent_from_training() -> None:
    observations = [
        _obs(
            report_code="R1", player="P1", class_name="Mage", spec_name="Frost", encounter_id=3178
        ),
        _obs(
            report_code="R2",
            player="P2",
            class_name="Priest",
            spec_name="Shadow",
            encounter_id=3177,
        ),
    ]
    split = held_out_spec_encounter_split(
        _dataset(observations), spec_key="Mage/Frost", encounter_id=3178
    )

    assert not spec_and_encounter_seen_separately(split, spec_key="Mage/Frost", encounter_id=3178)


# -- invariant enforcement --------------------------------------------------------


def test_a_contaminated_split_raises_rather_than_returning() -> None:
    """The guard that makes every other guarantee trustworthy."""
    shared = _obs(report_code="R1", player="P1", at_ms=100)
    with pytest.raises(SplitInvariantError, match="both sides"):
        from botgitgud.phase4.experiment_splits import _verify

        _verify(
            DataSplit(
                protocol=SplitProtocol.S1_TEMPORAL_WITHIN_TARGET,
                train=(shared,),
                validation=(shared,),
            ),
            temporal=False,
        )


def test_out_of_order_temporal_split_raises() -> None:
    from botgitgud.phase4.experiment_splits import _verify

    past = _obs(report_code="R1", player="P1", at_ms=500)
    future = _obs(report_code="R2", player="P2", at_ms=100)
    with pytest.raises(SplitInvariantError, match="before training ends"):
        _verify(
            DataSplit(
                protocol=SplitProtocol.S1_TEMPORAL_WITHIN_TARGET,
                train=(past,),
                validation=(future,),
            ),
            temporal=True,
        )


@pytest.mark.parametrize(
    "make_split",
    [
        lambda d: temporal_within_target_split(d),
        lambda d: held_out_logs_temporal_split(d),
        lambda d: held_out_encounter_split(d, encounter_id=3177),
        lambda d: held_out_spec_split(d, spec_key="Priest/Shadow"),
        lambda d: held_out_spec_encounter_split(d, spec_key="Mage/Frost", encounter_id=3178),
    ],
)
def test_no_protocol_ever_shares_an_observation(make_split: SplitFactory) -> None:
    split = make_split(_wide_dataset())
    train_keys = {o.observation_key for o in split.train}
    validation_keys = {o.observation_key for o in split.validation}
    assert not (train_keys & validation_keys)


@pytest.mark.parametrize(
    "make_split",
    [
        lambda d: temporal_within_target_split(d),
        lambda d: held_out_logs_temporal_split(d),
        lambda d: held_out_encounter_split(d, encounter_id=3177),
        lambda d: held_out_spec_split(d, spec_key="Priest/Shadow"),
        lambda d: held_out_spec_encounter_split(d, spec_key="Mage/Frost", encounter_id=3178),
    ],
)
def test_every_protocol_handles_an_empty_dataset(make_split: SplitFactory) -> None:
    split = make_split(_dataset([]))
    assert not split.is_usable
