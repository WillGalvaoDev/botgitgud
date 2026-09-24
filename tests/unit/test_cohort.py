from __future__ import annotations

from pathlib import Path

import pytest

from botgitgud.analysis.cohort import (
    COHORT_MIN_HARD,
    COHORT_MIN_WARN,
    classify_cohort_size,
    duration_bucket_bounds,
    duration_bucket_id,
    usage_count_at_duration,
    usage_rate_per_minute,
    within_positional_band,
    within_sanity_band,
)
from botgitgud.analysis.grading import MIN_N_FOR_GRADING


def test_target_is_derived_from_grading_threshold() -> None:
    from botgitgud.analysis.cohort import COHORT_TARGET_N

    assert COHORT_TARGET_N == MIN_N_FOR_GRADING


# -- documented acceptance criteria ------------------------------------------


def test_cohort_of_7_is_insufficient() -> None:
    assert classify_cohort_size(7) == "insufficient"
    assert COHORT_MIN_HARD == 8


def test_cohort_of_15_is_warn() -> None:
    assert classify_cohort_size(15) == "warn"
    assert COHORT_MIN_WARN == 20


def test_cohort_of_20_is_ok() -> None:
    assert classify_cohort_size(20) == "ok"


def test_invariant_metric_uses_whole_pool_within_sanity_band() -> None:
    """Uptime%/active-time%/damage-per-cast: docs/architecture.md's own
    normalization table says these use the whole ±35% pool, unadjusted.
    within_sanity_band is the gate that pool is built from.
    """
    target = 345.146
    assert within_sanity_band(224.4, target) is True  # just inside -35%
    assert within_sanity_band(224.3, target) is False  # just outside
    assert within_sanity_band(465.9, target) is True  # just inside +35%


def test_scaling_metric_compared_as_rate_not_absolute() -> None:
    """nº de casts / dano total: compared as a per-minute rate, not raw."""
    # Same rate (1 cast per 60s), different durations -> same rate value.
    rate_a = usage_rate_per_minute(usage_count=5, duration_s=300.0)  # 5 in 5min = 1/min
    rate_b = usage_rate_per_minute(usage_count=6, duration_s=360.0)  # 6 in 6min = 1/min
    assert rate_a == rate_b == 1.0

    # Raw counts (5 vs 6) would have looked different; the rate correctly
    # shows they're the same rotation intensity.
    assert 5 != 6

    # Rescaling that rate back to a specific duration recovers a comparable
    # absolute count for T0.6's classify_cd_type/is_eligible contract.
    assert usage_count_at_duration(1.0, 300.0) == 5.0


def test_real_fixture_zarad_produces_10_in_sanity_band_not_insufficient() -> None:
    """Teste de regressão da T0.8, com a distribuição real medida contra a
    API (encounter 3179, Warlock/Demonology, difficulty 5): 26 rankings no
    total, target=345.146s. Com o filtro absoluto antigo (±30s) restariam
    ~2; com a banda de sanidade ±35% relativa, restam 10 — suficiente para
    não disparar InsufficientCohort (>= 8), embora ainda abaixo do limiar
    de aviso (20).
    """
    target = 345.146
    durations = [
        145.6, 158.5, 158.5, 160.4, 177.8, 179.3, 182.7, 193.4, 193.4, 195.3,
        195.7, 195.8, 199.0, 204.8, 204.8, 217.6, 234.3, 237.0, 237.5, 239.4,
        240.9, 240.9, 271.6, 312.6, 355.8, 355.8,
    ]  # fmt: skip
    n_in_band = sum(1 for d in durations if within_sanity_band(d, target))
    assert n_in_band == 10
    assert classify_cohort_size(n_in_band) != "insufficient"
    assert classify_cohort_size(n_in_band) == "warn"  # 10 < 20, honesto sobre a amostra pequena


def test_positional_band_is_narrower_than_sanity_band() -> None:
    target = 345.146
    durations = [
        145.6, 158.5, 158.5, 160.4, 177.8, 179.3, 182.7, 193.4, 193.4, 195.3,
        195.7, 195.8, 199.0, 204.8, 204.8, 217.6, 234.3, 237.0, 237.5, 239.4,
        240.9, 240.9, 271.6, 312.6, 355.8, 355.8,
    ]  # fmt: skip
    n_sanity = sum(1 for d in durations if within_sanity_band(d, target))
    n_positional = sum(1 for d in durations if within_positional_band(d, target))
    assert n_positional <= n_sanity


def test_no_lexical_metric_branching_in_wired_files() -> None:
    """`grep -n '"hps"\\|healing' src/botgitgud/ingest/rankings.py` no
    documento originalmente apontava para um arquivo que só passou a
    existir na T1.6 (D-9); agora que ele existe, o critério é verificado
    contra `ingest/rankings.py` diretamente, mais os outros arquivos que
    a T1.6 (D-9's "Impacto") também identificou como devendo herdar essa
    lógica (`analysis/cohort.py`, `analysis/pipeline.py`).
    """
    repo_root = Path(__file__).resolve().parents[2]
    targets = [
        repo_root / "src" / "botgitgud" / "analysis" / "cohort.py",
        repo_root / "src" / "botgitgud" / "ingest" / "rankings.py",
        repo_root / "src" / "botgitgud" / "analysis" / "pipeline.py",
    ]
    offenders = []
    for path in targets:
        text = path.read_text(encoding="utf-8").lower()
        if '"hps"' in text or "healing" in text:
            offenders.append(path)
    assert offenders == []


# -- T1.7: duration buckets -----------------------------------------------------


def test_duration_bucket_id_is_deterministic() -> None:
    assert duration_bucket_id(345.1) == duration_bucket_id(345.1)


def test_durations_within_5pct_usually_share_a_bucket() -> None:
    base = 100.0  # comfortably inside a bucket's interior, not near a boundary
    assert duration_bucket_id(base) == duration_bucket_id(base * 1.02)


def test_durations_a_bucket_apart_get_different_ids() -> None:
    base = 100.0
    assert duration_bucket_id(base) != duration_bucket_id(base * 1.20)


def test_bucket_bounds_contain_every_duration_that_maps_to_that_bucket() -> None:
    for duration_s in (10.0, 60.0, 300.0, 345.1, 900.0, 3600.0):
        bucket_id = duration_bucket_id(duration_s)
        lo, hi = duration_bucket_bounds(bucket_id)
        assert lo <= duration_s < hi


def test_bucket_bounds_are_contiguous_across_neighbors() -> None:
    _lo, hi = duration_bucket_bounds(5)
    next_lo, _next_hi = duration_bucket_bounds(6)
    assert hi == next_lo


def test_bucket_width_is_five_percent_of_its_lower_bound() -> None:
    lo, hi = duration_bucket_bounds(10)
    assert (hi - lo) / lo == pytest.approx(0.05)


def test_zero_or_negative_duration_maps_to_bucket_zero() -> None:
    assert duration_bucket_id(0.0) == 0
    assert duration_bucket_id(-5.0) == 0
