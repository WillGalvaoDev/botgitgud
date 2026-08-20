from __future__ import annotations

from collections.abc import Iterator
from itertools import pairwise
from pathlib import Path

import httpx
import pytest

from botgitgud.domain.specs import SpecId
from botgitgud.ingest.discovery_store import DiscoveryStore
from botgitgud.ingest.store import Store
from botgitgud.phase4.experiment import ExperimentBudget
from botgitgud.phase4.experiment_campaign import StatisticalExperimentPlan
from botgitgud.phase4.experiment_planner import (
    ExperimentPlanner,
    balanced_visit_order,
    spread_order,
)

PARTITION = 4
DIFFICULTY = 5

# A synthetic census: 8 specs x 5 encounters x 5 percentile bands = 200
# strata — wide enough to exercise MIN_SPECS/MIN_ENCOUNTERS coverage, small
# enough that seeding stays cheap (DuckDB inserts cost ~3ms/row).
_SPECS = [
    ("DeathKnight", "Unholy"),
    ("Druid", "Balance"),
    ("Hunter", "BeastMastery"),
    ("Mage", "Frost"),
    ("Priest", "Shadow"),
    ("Rogue", "Subtlety"),
    ("Warlock", "Demonology"),
    ("Warrior", "Arms"),
]
_ENCOUNTERS = [3176, 3177, 3178, 3179, 3180]
_RANKS = [10.0, 30.0, 50.0, 70.0, 90.0]


def _seed(
    store: Store,
    *,
    specs: list[tuple[str, str]] | None = None,
    encounters: list[int] | None = None,
    ranks: list[float] | None = None,
    difficulty: int = DIFFICULTY,
    partition: int = PARTITION,
    kill: bool = True,
    per_cell: int = 1,
    report_prefix: str = "R",
    base_time_ms: int = 1_000_000,
) -> None:
    """One fight per (spec, encounter, rank) cell, each with `per_cell`
    players, at strictly increasing timestamps.

    Rows are bulk-inserted rather than written through DiscoveryStore's
    per-row API — the per-row write path is already covered by
    test_discovery_store.py; here only the schema matters, and one
    statement per row would dominate the suite's runtime.
    """
    DiscoveryStore(store)  # owns the DDL
    specs = specs if specs is not None else _SPECS
    encounters = encounters if encounters is not None else _ENCOUNTERS
    ranks = ranks if ranks is not None else _RANKS

    reports: list[tuple[object, ...]] = []
    fights: list[tuple[object, ...]] = []
    targets: list[tuple[object, ...]] = []
    n = 0
    for enc in encounters:
        for class_name, spec_name in specs:
            for rank in ranks:
                code = f"{report_prefix}{n:06d}"
                start = base_time_ms + n * 60_000
                reports.append((code, 46, start, start + 1))
                fights.append((code, 1, partition, enc, difficulty, 20, kill, 300.0))
                for i in range(per_cell):
                    targets.append(
                        (code, 1, f"P{n:06d}_{i}", class_name, spec_name, rank, 100000.0)
                    )
                n += 1

    _bulk_insert(
        store,
        "INSERT OR REPLACE INTO discovery_reports "
        "(report_code, zone_id, start_time_ms, end_time_ms) VALUES ",
        4,
        reports,
    )
    _bulk_insert(
        store,
        "INSERT OR REPLACE INTO discovery_fights (report_code, fight_id, partition, "
        "encounter_id, difficulty, size, kill, duration_s) VALUES ",
        8,
        fights,
    )
    _bulk_insert(
        store,
        "INSERT OR REPLACE INTO discovery_targets (report_code, fight_id, player_name, "
        "class_name, spec_name, rank_percent, amount) VALUES ",
        7,
        targets,
    )


def _bulk_insert(store: Store, prefix: str, width: int, rows: list[tuple[object, ...]]) -> None:
    if not rows:
        return
    placeholder = "(" + ", ".join("?" * width) + ")"
    sql = prefix + ", ".join([placeholder] * len(rows))
    store.execute(sql, [value for row in rows for value in row])


@pytest.fixture(scope="module")
def seeded_store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Store]:
    """Module-scoped: the planner is strictly read-only, so every test that
    uses the standard fixture can share one warehouse instead of paying the
    seeding cost 20+ times.
    """
    path = tmp_path_factory.mktemp("planner_seeded")
    with Store(path) as store:
        _seed(store)
        yield store


def _request(**overrides: object) -> StatisticalExperimentPlan:
    defaults: dict[str, object] = {
        "partition": PARTITION,
        "difficulties": frozenset({DIFFICULTY}),
        "budget": ExperimentBudget(max_api_points=25_000.0),
        "max_observations": 100,
    }
    defaults.update(overrides)
    return StatisticalExperimentPlan(**defaults)  # type: ignore[arg-type]


# -- spread_order --------------------------------------------------------------


def test_spread_order_is_a_permutation() -> None:
    for n in (0, 1, 2, 3, 7, 8, 50):
        assert sorted(spread_order(n)) == list(range(n))


@pytest.mark.parametrize("k", [1, 3, 7, 15, 31])
def test_spread_order_prefix_is_spread_not_clustered(k: int) -> None:
    """Any prefix must cover the whole range roughly evenly, not sit at the
    start: the largest gap between consecutive picks stays within 2x the
    ideal spacing.
    """
    n = 100
    picks = sorted(spread_order(n)[:k])
    boundaries = [-1, *picks, n]
    largest_gap = max(b - a for a, b in pairwise(boundaries))
    assert largest_gap <= 2 * n / k


def test_spread_order_is_deterministic() -> None:
    assert spread_order(37) == spread_order(37)


# -- balanced_visit_order ------------------------------------------------------


def test_balanced_visit_order_is_a_permutation() -> None:
    keys = [(f"S{s}", e, b) for s in range(4) for e in range(3) for b in ("00-20", "80-100")]
    assert sorted(balanced_visit_order(keys)) == sorted(keys)


def test_balanced_visit_order_prefix_covers_every_spec_before_repeating_one() -> None:
    """Plain sorted() would emit all of spec S0 first; a truncated sample
    would then contain only alphabetically-early specs.
    """
    keys = [(f"S{s}", e, b) for s in range(4) for e in range(3) for b in ("00-20", "80-100")]
    order = balanced_visit_order(keys)
    assert len({k[0] for k in order[:4]}) == 4


def test_balanced_visit_order_prefix_spreads_encounters_too() -> None:
    keys = [(f"S{s}", e, "00-20") for s in range(3) for e in range(3)]
    order = balanced_visit_order(keys)
    assert len({k[1] for k in order[:3]}) == 3


def test_balanced_visit_order_is_deterministic() -> None:
    keys = [(f"S{s}", e, b) for s in range(3) for e in range(3) for b in ("00-20", "40-60")]
    assert balanced_visit_order(keys) == balanced_visit_order(keys)


def test_balanced_visit_order_handles_empty_input() -> None:
    assert balanced_visit_order([]) == []


# -- budget --------------------------------------------------------------------


def test_planner_never_exceeds_the_budget(seeded_store: Store) -> None:
    budget = ExperimentBudget(max_api_points=340.0)  # 20 solo observations
    campaign = ExperimentPlanner(seeded_store).plan(
        _request(budget=budget, max_observations=10_000)
    )

    assert campaign.estimated_api_points <= 340.0
    assert campaign.stopped_reason == "budget_exhausted"
    assert campaign.n_observations == 20


def test_estimated_points_match_the_selected_shape(seeded_store: Store) -> None:
    campaign = ExperimentPlanner(seeded_store).plan(_request(max_observations=25))
    budget = campaign.request.budget
    expected = budget.estimate_points(
        n_fights=campaign.distinct_fights, n_observations=campaign.n_observations
    )
    assert campaign.estimated_api_points == expected


def test_max_observations_is_respected(seeded_store: Store) -> None:
    campaign = ExperimentPlanner(seeded_store).plan(_request(max_observations=13))
    assert campaign.n_observations == 13
    assert campaign.stopped_reason == "max_observations"


def test_budget_caps_observations_even_without_an_explicit_max(seeded_store: Store) -> None:
    budget = ExperimentBudget(max_api_points=170.0)  # 10 solo observations
    campaign = ExperimentPlanner(seeded_store).plan(_request(budget=budget, max_observations=None))
    assert campaign.n_observations <= 10
    assert campaign.stopped_reason == "budget_exhausted"


def test_without_an_explicit_max_a_generous_budget_drains_the_pool(
    seeded_store: Store,
) -> None:
    """Regression: the cap for `max_observations=None` must come from the
    pool, not from `max_observations_solo()`. Because observations sharing a
    fight cost 2 points instead of 17, a budget buys far more than the solo
    estimate — measured on the real census, the whole 7.333-observation
    mythic pool costs 23.426 points, not 124.661.
    """
    campaign = ExperimentPlanner(seeded_store).plan(
        _request(budget=ExperimentBudget(max_api_points=1_000_000.0), max_observations=None)
    )
    assert campaign.n_observations == campaign.candidates_available
    assert campaign.stopped_reason == "pool_exhausted"


# -- diversity and stratification ----------------------------------------------


def test_planner_spreads_across_multiple_specs(seeded_store: Store) -> None:
    campaign = ExperimentPlanner(seeded_store).plan(_request(max_observations=200))
    assert len(campaign.by_spec) == len(_SPECS)


def test_planner_spreads_across_multiple_encounters(seeded_store: Store) -> None:
    campaign = ExperimentPlanner(seeded_store).plan(_request(max_observations=200))
    assert len(campaign.by_encounter) == len(_ENCOUNTERS)


def test_planner_stratifies_rank_percent_buckets_evenly(seeded_store: Store) -> None:
    """Round-robin over strata: no bucket may be starved, and the sample is
    never "top parses only" (doc §7).
    """
    campaign = ExperimentPlanner(seeded_store).plan(_request(max_observations=200))
    buckets = campaign.by_bucket
    assert set(buckets) == {"00-20", "20-40", "40-60", "60-80", "80-100"}
    assert max(buckets.values()) - min(buckets.values()) <= 1


def test_a_truncated_sample_is_still_balanced_across_every_dimension(
    seeded_store: Store,
) -> None:
    """The failure mode `balanced_visit_order` exists to prevent: a sample
    far smaller than the stratum count must not collapse onto the
    alphabetically-first specs.
    """
    campaign = ExperimentPlanner(seeded_store).plan(_request(max_observations=40))
    assert len(campaign.by_spec) == len(_SPECS)
    assert len(campaign.by_encounter) == len(_ENCOUNTERS)
    assert len(campaign.by_bucket) == 5


def test_small_sample_still_touches_many_strata(seeded_store: Store) -> None:
    campaign = ExperimentPlanner(seeded_store).plan(_request(max_observations=30))
    assert campaign.strata_covered == 30  # every pick lands in a distinct stratum


def test_a_dominant_spec_does_not_swamp_the_sample(tmp_path: Path) -> None:
    """One spec with 10x the candidates must not take 10x the sample."""
    with Store(tmp_path) as store:
        _seed(store, specs=[("Mage", "Frost")], per_cell=20, report_prefix="BIG")
        _seed(
            store,
            specs=[("Priest", "Shadow")],
            per_cell=2,
            report_prefix="SML",
            base_time_ms=9_000_000,
        )
        campaign = ExperimentPlanner(store).plan(_request(max_observations=60))

    counts = campaign.by_spec
    assert counts["Priest/Shadow"] * 2 >= counts["Mage/Frost"]


# -- partition and difficulty isolation ----------------------------------------


def test_planner_never_mixes_partitions(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        _seed(store, encounters=[3176], partition=4)
        _seed(store, encounters=[3176], partition=2, report_prefix="P2", base_time_ms=5_000_000)
        campaign = ExperimentPlanner(store).plan(_request(max_observations=500))

    assert {o.partition for o in campaign.observations} == {4}


def test_planner_never_mixes_unrequested_difficulties(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        _seed(store, encounters=[3176], difficulty=5)
        _seed(store, encounters=[3176], difficulty=3, report_prefix="D3", base_time_ms=5_000_000)
        campaign = ExperimentPlanner(store).plan(
            _request(difficulties=frozenset({5}), max_observations=500)
        )

    assert {o.difficulty for o in campaign.observations} == {5}


def test_planner_can_include_several_difficulties_when_asked(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        _seed(store, encounters=[3176], difficulty=5)
        _seed(store, encounters=[3176], difficulty=4, report_prefix="D4", base_time_ms=5_000_000)
        campaign = ExperimentPlanner(store).plan(
            _request(difficulties=frozenset({4, 5}), max_observations=500)
        )

    assert {o.difficulty for o in campaign.observations} == {4, 5}


def test_request_rejects_empty_difficulties() -> None:
    with pytest.raises(ValueError, match="difficulties"):
        _request(difficulties=frozenset())


def test_request_rejects_negative_partition() -> None:
    with pytest.raises(ValueError, match="partition"):
        _request(partition=-1)


def test_request_rejects_non_positive_max_observations() -> None:
    with pytest.raises(ValueError, match="max_observations"):
        _request(max_observations=0)


# -- filtering -----------------------------------------------------------------


def test_only_kills_are_candidates(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        _seed(store, encounters=[3176], kill=False)
        campaign = ExperimentPlanner(store).plan(_request())

    assert campaign.n_observations == 0
    assert campaign.candidates_available == 0


def test_unsupported_specs_are_excluded(tmp_path: Path) -> None:
    """Augmentation is out of scope as a subject (T0.9/implementacao.md)."""
    with Store(tmp_path) as store:
        _seed(store, specs=[("Evoker", "Augmentation"), ("Mage", "Frost")], encounters=[3176])
        campaign = ExperimentPlanner(store).plan(_request(max_observations=200))

    assert set(campaign.by_spec) == {"Mage/Frost"}


def test_explicit_spec_filter_is_honoured(seeded_store: Store) -> None:
    campaign = ExperimentPlanner(seeded_store).plan(
        _request(specs=frozenset({SpecId("Mage", "Frost")}), max_observations=200)
    )
    assert set(campaign.by_spec) == {"Mage/Frost"}


def test_explicit_encounter_filter_is_honoured(seeded_store: Store) -> None:
    campaign = ExperimentPlanner(seeded_store).plan(
        _request(encounters=frozenset({3176, 3177}), max_observations=200)
    )
    assert set(campaign.by_encounter) == {3176, 3177}


# -- determinism, dedup, purity -------------------------------------------------


def test_planner_is_deterministic(seeded_store: Store) -> None:
    planner = ExperimentPlanner(seeded_store)
    first = planner.plan(_request(max_observations=77))
    second = planner.plan(_request(max_observations=77))

    assert [o.observation_key for o in first.observations] == [
        o.observation_key for o in second.observations
    ]
    assert first.estimated_api_points == second.estimated_api_points


def test_planner_never_selects_the_same_observation_twice(seeded_store: Store) -> None:
    campaign = ExperimentPlanner(seeded_store).plan(_request(max_observations=500))
    keys = [o.observation_key for o in campaign.observations]
    assert len(keys) == len(set(keys))


def test_planner_makes_no_api_call(seeded_store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    """Doc §16: the planner is read-only. Any outbound HTTP fails the test."""

    def _explode(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("planner must never perform HTTP")

    monkeypatch.setattr(httpx.Client, "send", _explode)
    monkeypatch.setattr(httpx.Client, "request", _explode)

    campaign = ExperimentPlanner(seeded_store).plan(_request(max_observations=50))
    assert campaign.n_observations == 50


def test_empty_warehouse_yields_an_empty_campaign(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        DiscoveryStore(store)
        campaign = ExperimentPlanner(store).plan(_request())

    assert campaign.n_observations == 0
    assert campaign.candidates_available == 0
    assert campaign.stopped_reason == "pool_exhausted"
    assert campaign.temporal_span_ms is None
    assert campaign.meets_minimum_coverage is False


# -- campaign reporting ---------------------------------------------------------


def test_campaign_reports_targets_and_temporal_span(seeded_store: Store) -> None:
    campaign = ExperimentPlanner(seeded_store).plan(_request(max_observations=200))

    assert len(campaign.by_target) == len(_SPECS) * len(_ENCOUNTERS)
    span = campaign.temporal_span_ms
    assert span is not None
    assert span[0] < span[1]


def test_campaign_meets_minimum_coverage_with_a_wide_sample(seeded_store: Store) -> None:
    campaign = ExperimentPlanner(seeded_store).plan(_request(max_observations=200))
    assert campaign.meets_minimum_coverage is True


def test_campaign_flags_insufficient_coverage_for_a_narrow_sample(tmp_path: Path) -> None:
    with Store(tmp_path) as store:
        _seed(store, specs=[("Mage", "Frost")], encounters=[3176])
        campaign = ExperimentPlanner(store).plan(_request(max_observations=50))

    assert campaign.meets_minimum_coverage is False


def test_observation_exposes_its_phase4_target(seeded_store: Store) -> None:
    campaign = ExperimentPlanner(seeded_store).plan(
        _request(
            specs=frozenset({SpecId("Mage", "Frost")}),
            encounters=frozenset({3176}),
            max_observations=1,
        )
    )
    assert campaign.observations[0].target.target_id == f"Mage/Frost/3176/{DIFFICULTY}/{PARTITION}"


def test_sharing_a_fight_costs_less_than_two_separate_fights(tmp_path: Path) -> None:
    """Two players of the same fight share the event pages (§8.3)."""
    with Store(tmp_path) as store:
        _seed(store, specs=[("Mage", "Frost")], encounters=[3176], ranks=[50.0], per_cell=2)
        campaign = ExperimentPlanner(store).plan(_request(max_observations=2))

    assert campaign.n_observations == 2
    assert campaign.distinct_fights == 1
    assert campaign.estimated_api_points == 19.0
