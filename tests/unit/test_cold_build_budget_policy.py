"""Correcao de budget semantics: a politica anterior era matematicamente
impossivel.

Com limit=3600, estimated=3012, floor=1000, reserve=1000, margin=250, iniciar
um cold build exigia 4262 pontos numa conta cujo teto e 3600 — nenhum build
podia ser autorizado nunca, prewarm inclusive, e o sintoma aparecia apenas como
um COHORT_DEFERRED_BUDGET eterno.

Nada aqui toca a rede.
"""

from __future__ import annotations

import pytest

from botgitgud.analysis.cold_build import (
    ColdBuildMode,
    ImpossibleColdBuildPolicy,
    affordable_references,
    estimate_cold_build,
    validate_cold_build_policy,
)
from botgitgud.config import Settings

LIMIT_PER_HOUR = 3600.0


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "discord_token": "d" * 10,
        "wcl_client_id": "id",
        "wcl_client_secret": "secret",
        "blizzard_client_id": "id2",
        "blizzard_client_secret": "secret2",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type, call-arg]


# -- 12: a regressao da contradicao ------------------------------------------------


def test_prewarm_can_always_make_progress_within_the_account_ceiling() -> None:
    """Falharia contra 5b612de: la, nenhum numero de referencias era permitido
    em nenhum nivel de orcamento possivel.
    """
    settings = _settings()
    affordable = affordable_references(
        settings, LIMIT_PER_HOUR, mode=ColdBuildMode.PREWARM, planned=settings.cohort_max
    )
    assert affordable > 0


def test_minimal_prewarm_increment_fits_the_account_ceiling() -> None:
    validate_cold_build_policy(_settings(), LIMIT_PER_HOUR)  # nao levanta


def test_impossible_policy_is_diagnosed_instead_of_deferring_forever() -> None:
    """Uma configuracao que nao cabe no teto deve ser ERRO DE CONFIGURACAO
    visivel, nunca um defer silencioso e eterno.
    """
    hostile = _settings(api_points_floor=3500.0, cold_build_safety_margin=250.0)
    with pytest.raises(ImpossibleColdBuildPolicy, match="impossivel"):
        validate_cold_build_policy(hostile, LIMIT_PER_HOUR)


# -- 5: cost model calibrado -------------------------------------------------------


def test_cost_model_uses_the_measured_points_per_query_not_a_flat_two() -> None:
    settings = _settings()
    assert settings.cold_build_points_per_query == pytest.approx(1.413)
    # A banda e derivada: sobrescrever o custo esperado escala o limite
    # superior junto, entao os dois nunca divergem em silencio.
    assert settings.cold_build_cost_uncertainty > 1.0


def test_band_scales_with_the_base_constant() -> None:
    cheap = estimate_cold_build(_settings(cold_build_points_per_query=0.5), 3600.0)
    assert cheap.estimated_upper_bound == pytest.approx(cheap.estimated_api_points * 1.2)


def test_estimate_exposes_an_explicit_band() -> None:
    cost = estimate_cold_build(_settings(), 3600.0)
    assert cost.estimated_api_points < cost.estimated_upper_bound
    # A decisao usa o limite superior, nao o esperado.
    assert cost.projected_remaining == cost.available_api_points - cost.estimated_upper_bound


def test_measured_smoke_cost_falls_inside_the_band() -> None:
    """1.504 queries custaram ~2.126 pontos no smoke real (1,413 pts/query)."""
    settings = _settings()
    cost = estimate_cold_build(settings, 3600.0, references=100)
    assert cost.estimated_api_points <= 2200
    assert cost.estimated_upper_bound >= 2500  # banda conservadora


# -- 1/2/3: modos distintos ---------------------------------------------------------


def test_interactive_protects_the_hot_reserve_and_prewarm_does_not() -> None:
    settings = _settings()
    interactive = estimate_cold_build(settings, 3600.0, mode=ColdBuildMode.INTERACTIVE)
    prewarm = estimate_cold_build(settings, 3600.0, mode=ColdBuildMode.PREWARM)
    assert interactive.protected_floor == max(settings.api_points_floor, settings.hot_path_reserve)
    assert prewarm.protected_floor == settings.api_points_floor
    assert prewarm.safety_margin < interactive.safety_margin


def test_neither_mode_ever_plans_below_the_api_floor() -> None:
    settings = _settings()
    for available in (3600.0, 3000.0, 2000.0, 1500.0, 1100.0, 900.0):
        for mode in (ColdBuildMode.INTERACTIVE, ColdBuildMode.PREWARM):
            n = affordable_references(settings, available, mode=mode, planned=100)
            if n == 0:
                continue
            cost = estimate_cold_build(settings, available, mode=mode, references=n)
            assert cost.projected_remaining >= settings.api_points_floor


# -- 11: simulacoes obrigatorias -----------------------------------------------------


@pytest.mark.parametrize(
    ("available", "expect_progress"),
    [(3600.0, True), (3000.0, True), (2000.0, True), (1500.0, True), (1100.0, False)],
)
def test_prewarm_progress_simulation(available: float, expect_progress: bool) -> None:
    n = affordable_references(_settings(), available, mode=ColdBuildMode.PREWARM, planned=100)
    assert (n > 0) is expect_progress


def test_interactive_defers_when_the_hot_reserve_would_be_eaten() -> None:
    """hot path > cold convenience: um build interativo completo nao cabe."""
    settings = _settings()
    cost = estimate_cold_build(settings, 3600.0, mode=ColdBuildMode.INTERACTIVE)
    assert cost.allowed is False


def test_a_small_interactive_build_is_still_allowed_when_budget_is_ample() -> None:
    settings = _settings()
    cost = estimate_cold_build(settings, 3600.0, mode=ColdBuildMode.INTERACTIVE, references=5)
    assert cost.allowed is True


def test_budget_at_the_floor_allows_no_work_at_all() -> None:
    settings = _settings()
    for mode in (ColdBuildMode.INTERACTIVE, ColdBuildMode.PREWARM):
        assert affordable_references(settings, 1000.0, mode=mode, planned=100) == 0


def test_affordable_never_exceeds_what_was_planned() -> None:
    assert affordable_references(_settings(), 3600.0, mode=ColdBuildMode.PREWARM, planned=7) == 7
