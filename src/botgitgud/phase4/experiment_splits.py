"""SAE.4 — validation split protocols S1-S5
(docs/fase4-statistical-architecture-experiment.md §8).

The centre of the experiment: the granularity question is only answerable
if the validation protocol actually isolates what it claims to. A plain
random split is deliberately not offered — it is never the primary
validation here, because two players of the same pull, or the same player
across pulls, would sit on both sides and inflate every score.

Two families:

* **S1/S2 are temporal.** Train is strictly the past, validation strictly
  the future. S2 additionally forbids any shared report or player, so a
  model cannot score well by memorizing individuals.
* **S3/S4/S5 are dimension hold-outs.** An entire encounter, spec, or
  spec x encounter combination is absent from training. `temporal_cutoff_ms`
  is optional here: without it the split answers "does this transfer across
  the dimension at all", with it the split additionally refuses to train on
  anything recorded after validation began.

Every constructor verifies its own invariants and raises rather than
returning a subtly contaminated split — a silent leak would invalidate
every number the experiment produces.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from botgitgud.phase4.experiment import SplitProtocol
from botgitgud.phase4.experimental_dataset import (
    ExperimentalFeatureDataset,
    ExperimentalObservation,
)

Observations = tuple[ExperimentalObservation, ...]

DEFAULT_VALIDATION_FRACTION = 0.25


class SplitInvariantError(AssertionError):
    """A split violated its own contract. Never caught: a contaminated split
    makes every downstream metric meaningless.
    """


@dataclass(frozen=True, slots=True)
class DataSplit:
    protocol: SplitProtocol
    train: Observations
    validation: Observations
    held_out: str | None = None

    @property
    def n_train(self) -> int:
        return len(self.train)

    @property
    def n_validation(self) -> int:
        return len(self.validation)

    @property
    def is_usable(self) -> bool:
        """Both sides non-empty. A split with an empty side is not a failure
        of the code — a held-out spec may simply have no data — but it
        cannot be evaluated.
        """
        return bool(self.train) and bool(self.validation)

    @property
    def train_targets(self) -> frozenset[str]:
        return frozenset(o.target.target_id for o in self.train)

    @property
    def validation_targets(self) -> frozenset[str]:
        return frozenset(o.target.target_id for o in self.validation)

    @property
    def unseen_validation_targets(self) -> frozenset[str]:
        """Targets evaluated but never trained on — the population the
        shared-model hypotheses (H3/H4) live or die by.
        """
        return self.validation_targets - self.train_targets


def _check_disjoint(split: DataSplit) -> None:
    train_keys = {o.observation_key for o in split.train}
    validation_keys = {o.observation_key for o in split.validation}
    overlap = train_keys & validation_keys
    if overlap:
        raise SplitInvariantError(
            f"{split.protocol}: {len(overlap)} observation(s) on both sides, e.g. {min(overlap)}"
        )


def _check_temporal_order(split: DataSplit) -> None:
    if not split.train or not split.validation:
        return
    latest_train = max(o.observed_at_ms for o in split.train)
    earliest_validation = min(o.observed_at_ms for o in split.validation)
    if earliest_validation < latest_train:
        raise SplitInvariantError(
            f"{split.protocol}: validation starts at {earliest_validation} "
            f"before training ends at {latest_train}"
        )


def _verify(split: DataSplit, *, temporal: bool) -> DataSplit:
    _check_disjoint(split)
    if temporal:
        _check_temporal_order(split)
    return split


def _temporal_cut(ordered: Observations, validation_fraction: float) -> tuple[Observations, int]:
    """Index of the first validation row, pushed back so that rows sharing
    the boundary timestamp all land in validation — otherwise the same
    instant would straddle the cut and break the ordering invariant.
    """
    if not 0.0 < validation_fraction < 1.0:
        raise ValueError("validation_fraction must be strictly between 0 and 1")
    cut = int(len(ordered) * (1.0 - validation_fraction))
    if cut <= 0 or cut >= len(ordered):
        return ordered, cut
    boundary = ordered[cut].observed_at_ms
    while cut > 0 and ordered[cut - 1].observed_at_ms == boundary:
        cut -= 1
    return ordered, cut


def temporal_within_target_split(
    dataset: ExperimentalFeatureDataset,
    *,
    validation_fraction: float = DEFAULT_VALIDATION_FRACTION,
) -> DataSplit:
    """S1 — train on the past, validate on the future of the same targets."""
    ordered, cut = _temporal_cut(dataset.sorted_by_time(), validation_fraction)
    split = DataSplit(
        protocol=SplitProtocol.S1_TEMPORAL_WITHIN_TARGET,
        train=ordered[:cut],
        validation=ordered[cut:],
    )
    return _verify(split, temporal=True)


def held_out_logs_temporal_split(
    dataset: ExperimentalFeatureDataset,
    *,
    validation_fraction: float = DEFAULT_VALIDATION_FRACTION,
) -> DataSplit:
    """S2 — S1 plus disjoint reports and players.

    Contaminated validation rows are dropped rather than moved into
    training: moving them would push future data into the past side and
    break the temporal invariant, which matters more than sample size.
    """
    ordered, cut = _temporal_cut(dataset.sorted_by_time(), validation_fraction)
    train = ordered[:cut]
    train_reports = {o.report_code for o in train}
    train_players = {o.player_name for o in train}
    validation = tuple(
        o
        for o in ordered[cut:]
        if o.report_code not in train_reports and o.player_name not in train_players
    )
    split = DataSplit(
        protocol=SplitProtocol.S2_HELD_OUT_LOGS_TEMPORAL, train=train, validation=validation
    )
    _verify(split, temporal=True)
    _check_no_shared_reports_or_players(split)
    return split


def _check_no_shared_reports_or_players(split: DataSplit) -> None:
    shared_reports = {o.report_code for o in split.train} & {
        o.report_code for o in split.validation
    }
    if shared_reports:
        raise SplitInvariantError(f"{split.protocol}: shared reports {sorted(shared_reports)[:3]}")
    shared_players = {o.player_name for o in split.train} & {
        o.player_name for o in split.validation
    }
    if shared_players:
        raise SplitInvariantError(f"{split.protocol}: shared players {sorted(shared_players)[:3]}")


def _dimension_split(
    dataset: ExperimentalFeatureDataset,
    *,
    protocol: SplitProtocol,
    held_out: str,
    is_held_out: Callable[[ExperimentalObservation], bool],
    temporal_cutoff_ms: int | None,
) -> DataSplit:
    train = tuple(o for o in dataset.sorted_by_time() if not is_held_out(o))
    validation = tuple(o for o in dataset.sorted_by_time() if is_held_out(o))
    if temporal_cutoff_ms is not None:
        train = tuple(o for o in train if o.observed_at_ms < temporal_cutoff_ms)
        validation = tuple(o for o in validation if o.observed_at_ms >= temporal_cutoff_ms)
    split = DataSplit(protocol=protocol, train=train, validation=validation, held_out=held_out)
    return _verify(split, temporal=temporal_cutoff_ms is not None)


def held_out_encounter_split(
    dataset: ExperimentalFeatureDataset,
    *,
    encounter_id: int,
    temporal_cutoff_ms: int | None = None,
) -> DataSplit:
    """S3 — the validation encounter has zero rows in training (H3)."""
    split = _dimension_split(
        dataset,
        protocol=SplitProtocol.S3_HELD_OUT_ENCOUNTER,
        held_out=str(encounter_id),
        is_held_out=lambda o: o.target.encounter_id == encounter_id,
        temporal_cutoff_ms=temporal_cutoff_ms,
    )
    if any(o.target.encounter_id == encounter_id for o in split.train):
        raise SplitInvariantError(f"S3: encounter {encounter_id} leaked into training")
    return split


def held_out_spec_split(
    dataset: ExperimentalFeatureDataset,
    *,
    spec_key: str,
    temporal_cutoff_ms: int | None = None,
) -> DataSplit:
    """S4 — the validation spec has zero rows in training (H4)."""
    split = _dimension_split(
        dataset,
        protocol=SplitProtocol.S4_HELD_OUT_SPEC,
        held_out=spec_key,
        is_held_out=lambda o: o.spec_key == spec_key,
        temporal_cutoff_ms=temporal_cutoff_ms,
    )
    if any(o.spec_key == spec_key for o in split.train):
        raise SplitInvariantError(f"S4: spec {spec_key} leaked into training")
    return split


def held_out_spec_encounter_split(
    dataset: ExperimentalFeatureDataset,
    *,
    spec_key: str,
    encounter_id: int,
    temporal_cutoff_ms: int | None = None,
) -> DataSplit:
    """S5 — the combination is absent from training, while the spec and the
    encounter each appear on their own. Tests interaction rather than either
    dimension alone; if training lacks the spec or the encounter separately
    the split degenerates into S4/S3 and `is_usable` is the caller's guard.
    """
    split = _dimension_split(
        dataset,
        protocol=SplitProtocol.S5_HELD_OUT_SPEC_ENCOUNTER,
        held_out=f"{spec_key}@{encounter_id}",
        is_held_out=lambda o: o.spec_key == spec_key and o.target.encounter_id == encounter_id,
        temporal_cutoff_ms=temporal_cutoff_ms,
    )
    leaked = [
        o for o in split.train if o.spec_key == spec_key and o.target.encounter_id == encounter_id
    ]
    if leaked:
        raise SplitInvariantError(f"S5: combination {spec_key}@{encounter_id} leaked into training")
    return split


def spec_and_encounter_seen_separately(
    split: DataSplit, *, spec_key: str, encounter_id: int
) -> bool:
    """S5's precondition: the model must have seen the spec and the encounter
    individually, otherwise the result says nothing about the interaction.
    """
    return any(o.spec_key == spec_key for o in split.train) and any(
        o.target.encounter_id == encounter_id for o in split.train
    )
