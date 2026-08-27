"""EB.2 — agregação offline de uma população de observações em um Encounter
Benchmark. 100% síntese em memória: nenhum arquivo, nenhuma rede.

Cenário central (inspirado no incidente real que motivou a revisão
arquitetural): 50 jogadores, 10 com uma build ruim, 40 com uma build boa —
o benchmark tem que refletir a população observada por banda, sem filtrar
pelo setup de ninguém.
"""

from __future__ import annotations

import random

import pytest

from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget, PercentileBand
from botgitgud.analysis.benchmark_aggregate import (
    EncounterBenchmark,
    EncounterBenchmarkAggregationError,
    build_encounter_benchmark,
    talent_build_key,
)
from botgitgud.domain.models import (
    FightRef,
    GearPiece,
    PlayerBuild,
    PlayerLog,
    SetupProfile,
    TalentNode,
)
from botgitgud.domain.specs import SpecId

TARGET = EncounterBenchmarkTarget(
    spec=SpecId("Warlock", "Demonology"), encounter_id=3179, difficulty=5, partition=3
)

BAD_BUILD = (TalentNode(100, 1, 900), TalentNode(101, 1, 901))
GOOD_BUILD = (TalentNode(200, 1, 950), TalentNode(201, 1, 951))


def _log(
    *,
    name: str,
    percentile: float | None,
    setup: SetupProfile | None = None,
    server: str = "Azralon",
    item_level: float | None = 289.0,
    duration_s: float = 300.0,
    report_code: str | None = None,
    fight_id: int = 1,
) -> PlayerLog:
    return PlayerLog(
        fight=FightRef(
            report_code or f"report-{name}",
            fight_id,
            TARGET.encounter_id,
            "Boss",
            TARGET.difficulty,
            duration_s,
            True,
            partition=TARGET.partition,
        ),
        build=PlayerBuild(
            character_name=name,
            server=server,
            class_name=TARGET.spec.class_name,
            spec_name=TARGET.spec.spec_name,
            role="dps",
            item_level=item_level,
            talent_hash=None,
            tier_pieces=None,
            setup=setup,
        ),
        dps=100_000.0,
        percentile=percentile,
        cast_timeline={},
    )


def _setup(talents: tuple[TalentNode, ...] = (), **kwargs: object) -> SetupProfile:
    defaults: dict[str, object] = {"talents": talents, "gear": (), "stats": {}}
    defaults.update(kwargs)
    return SetupProfile(**defaults)  # type: ignore[arg-type]


# -- 1: população sintética simples / cenário central (10 BAD / 40 GOOD) ----


def _population_50() -> list[PlayerLog]:
    """10 BAD espalhados entre as 3 bandas (não só no topo, nem só na
    base) + 40 GOOD preenchendo o resto — prova que nenhum filtro por
    setup individual acontece: um BAD que teve execução boa aparece no
    p95-99 do mesmo jeito.
    """
    logs = []
    # p95-99: 10 jogadores, 2 BAD
    for i in range(10):
        build = BAD_BUILD if i < 2 else GOOD_BUILD
        logs.append(_log(name=f"top{i}", percentile=95.0 + i * 0.35, setup=_setup(build)))
    # p75-95: 15 jogadores, 3 BAD
    for i in range(15):
        build = BAD_BUILD if i < 3 else GOOD_BUILD
        logs.append(_log(name=f"mid{i}", percentile=75.0 + i * (19.5 / 15), setup=_setup(build)))
    # p50-75: 25 jogadores, 5 BAD
    for i in range(25):
        build = BAD_BUILD if i < 5 else GOOD_BUILD
        logs.append(_log(name=f"low{i}", percentile=50.0 + i * (24.5 / 25), setup=_setup(build)))
    assert len(logs) == 50
    return logs


def test_population_scenario_reflects_observed_population_not_a_target_players_build() -> None:
    """1 + cenário obrigatório 10 BAD / 40 GOOD."""
    result = build_encounter_benchmark(
        _population_50(), target=TARGET, policy=BenchmarkPolicy.default()
    )

    assert result.total_input_observations == 50
    assert result.eligible_observations == 50
    assert result.missing_setup_count == 0

    bad_key = talent_build_key(_setup(BAD_BUILD))
    good_key = talent_build_key(_setup(GOOD_BUILD))
    assert bad_key is not None
    assert good_key is not None

    top = result.bands["p95-99"]
    assert top.sample_size == 10
    top_counts = {e.key: e.n_observed for e in top.talent_build_prevalence.entries}
    assert top_counts[bad_key] == 2  # a build ruim NÃO desaparece por ter ido bem
    assert top_counts[good_key] == 8

    mid = result.bands["p75-95"]
    mid_counts = {e.key: e.n_observed for e in mid.talent_build_prevalence.entries}
    assert mid_counts[bad_key] == 3

    low = result.bands["p50-75"]
    low_counts = {e.key: e.n_observed for e in low.talent_build_prevalence.entries}
    assert low_counts[bad_key] == 5


def test_no_target_player_parameter_exists_in_the_public_api() -> None:
    """Garantia estrutural: a função não tem NENHUM jeito de receber um
    "jogador alvo" para filtrar contra.
    """
    import inspect

    sig = inspect.signature(build_encounter_benchmark)
    names = set(sig.parameters)
    assert names == {"observations", "target", "policy"}


# -- 2/3: prevalência de talents / por banda ---------------------------------


def test_talent_prevalence_is_scoped_per_band_independently() -> None:
    logs = [
        _log(name="a", percentile=96.0, setup=_setup(GOOD_BUILD)),
        _log(name="b", percentile=97.0, setup=_setup(GOOD_BUILD)),
        _log(name="c", percentile=60.0, setup=_setup(BAD_BUILD)),
    ]
    result = build_encounter_benchmark(logs, target=TARGET, policy=BenchmarkPolicy.default())

    top_keys = {e.key for e in result.bands["p95-99"].talent_build_prevalence.entries}
    low_keys = {e.key for e in result.bands["p50-75"].talent_build_prevalence.entries}
    assert top_keys == {talent_build_key(_setup(GOOD_BUILD))}
    assert low_keys == {talent_build_key(_setup(BAD_BUILD))}


# -- 4: mesma build em ordem diferente -> mesma identidade -------------------


def test_same_build_in_different_node_order_has_the_same_identity() -> None:
    a = _setup((TalentNode(200, 1), TalentNode(100, 1)))
    b = _setup((TalentNode(100, 1), TalentNode(200, 1)))
    assert talent_build_key(a) == talent_build_key(b)


def test_spell_id_does_not_affect_build_identity() -> None:
    """spell_id é metadado auxiliar — mesmo (node_id, rank), spell_id
    diferente (ou ausente) ainda é a MESMA build.
    """
    a = _setup((TalentNode(100, 1, spell_id=900),))
    b = _setup((TalentNode(100, 1, spell_id=None),))
    c = _setup((TalentNode(100, 1, spell_id=12345),))
    assert talent_build_key(a) == talent_build_key(b) == talent_build_key(c)


def test_different_rank_is_a_different_build() -> None:
    a = _setup((TalentNode(100, 1),))
    b = _setup((TalentNode(100, 2),))
    assert talent_build_key(a) != talent_build_key(b)


def test_no_talents_has_no_build_identity() -> None:
    assert talent_build_key(_setup(())) is None


# -- 5/6: trinket individual e par canônico -----------------------------------


def _trinket_setup(a_id: int, b_id: int) -> SetupProfile:
    return _setup(gear=(GearPiece(12, a_id, 298.0, None), GearPiece(13, b_id, 298.0, None)))


def test_individual_trinket_prevalence() -> None:
    logs = [
        _log(name="a", percentile=96.0, setup=_trinket_setup(111, 222)),
        _log(name="b", percentile=97.0, setup=_trinket_setup(111, 333)),
        _log(name="c", percentile=98.0, setup=_trinket_setup(444, 555)),
    ]
    result = build_encounter_benchmark(logs, target=TARGET, policy=BenchmarkPolicy.default())
    band = result.bands["p95-99"]
    counts = {e.key: e.n_observed for e in band.trinket_prevalence.entries}
    assert counts["111"] == 2
    assert band.trinket_prevalence.n_available == 3


def test_trinket_pair_is_canonical_regardless_of_slot_order() -> None:
    a = _trinket_setup(111, 222)  # slot 12=111, slot 13=222
    b = _setup(gear=(GearPiece(12, 222, 298.0, None), GearPiece(13, 111, 298.0, None)))
    from botgitgud.analysis.benchmark_aggregate import _trinket_pair_key

    assert _trinket_pair_key(a) == _trinket_pair_key(b)


def test_trinket_pair_prevalence_requires_exactly_two_trinkets() -> None:
    one_trinket = _setup(gear=(GearPiece(12, 111, 298.0, None),))
    logs = [
        _log(name="a", percentile=96.0, setup=_trinket_setup(111, 222)),
        _log(name="b", percentile=97.0, setup=one_trinket),
    ]
    result = build_encounter_benchmark(logs, target=TARGET, policy=BenchmarkPolicy.default())
    band = result.bands["p95-99"]
    assert band.trinket_pair_prevalence.n_available == 1  # só "a" tem os 2 slots
    assert band.trinket_pair_prevalence.entries[0].key == "111+222"


def test_same_trinket_in_both_slots_counts_the_player_once() -> None:
    same = _trinket_setup(999, 999)
    logs = [_log(name="a", percentile=96.0, setup=same)]
    result = build_encounter_benchmark(logs, target=TARGET, policy=BenchmarkPolicy.default())
    entries = result.bands["p95-99"].trinket_prevalence.entries
    assert len(entries) == 1
    assert entries[0].n_observed == 1  # nunca 2 — prevalence nunca passa de 100%
    assert entries[0].prevalence == 1.0


def test_same_trinket_item_different_ilvl_is_the_same_identity() -> None:
    low_ilvl = _setup(gear=(GearPiece(12, 111, 285.0, None), GearPiece(13, 222, 285.0, None)))
    high_ilvl = _setup(gear=(GearPiece(12, 111, 298.0, None), GearPiece(13, 222, 298.0, None)))
    logs = [
        _log(name="a", percentile=96.0, setup=low_ilvl),
        _log(name="b", percentile=97.0, setup=high_ilvl),
    ]
    result = build_encounter_benchmark(logs, target=TARGET, policy=BenchmarkPolicy.default())
    counts = {e.key: e.n_observed for e in result.bands["p95-99"].trinket_prevalence.entries}
    assert counts["111"] == 2  # mesma identidade, apesar do ilvl diferente


# -- 7: setIDs separados corretamente ------------------------------------------


def test_set_ids_are_tallied_separately_not_merged() -> None:
    """O achado do EB.0: o cassete real tem 2 setIDs distintos no mesmo
    gear. `count_tier_pieces` legado somaria os dois; EB.2 não pode.
    """
    setup = _setup(
        gear=(
            GearPiece(0, 1, 298.0, 1983),
            GearPiece(2, 2, 298.0, 1983),
            GearPiece(6, 3, 298.0, 1958),
        )
    )
    logs = [_log(name="a", percentile=96.0, setup=setup)]
    result = build_encounter_benchmark(logs, target=TARGET, policy=BenchmarkPolicy.default())
    entries = {e.set_id: e.total_pieces for e in result.bands["p95-99"].set_summary.entries}
    assert entries == {"1983": 2, "1958": 1}


def test_set_summary_player_count_vs_total_pieces() -> None:
    two_pieces = _setup(gear=(GearPiece(0, 1, 298.0, 1983), GearPiece(2, 2, 298.0, 1983)))
    one_piece = _setup(gear=(GearPiece(0, 3, 298.0, 1983),))
    logs = [
        _log(name="a", percentile=96.0, setup=two_pieces),
        _log(name="b", percentile=97.0, setup=one_piece),
    ]
    result = build_encounter_benchmark(logs, target=TARGET, policy=BenchmarkPolicy.default())
    entry = result.bands["p95-99"].set_summary.entries[0]
    assert entry.total_pieces == 3  # 2 + 1
    assert entry.n_players == 2  # ambos usam >=1 peça do set
    assert entry.prevalence == 1.0


def test_zero_set_pieces_is_a_real_state_not_missing_data() -> None:
    no_sets = _setup(gear=(GearPiece(0, 1, 298.0, None),))
    logs = [_log(name="a", percentile=96.0, setup=no_sets)]
    result = build_encounter_benchmark(logs, target=TARGET, policy=BenchmarkPolicy.default())
    summary = result.bands["p95-99"].set_summary
    assert summary.n_available == 1  # setup existe -> conta no denominador
    assert summary.entries == ()  # mas nenhum setID observado


# -- 8: stats median/p25/p75 --------------------------------------------------


def test_secondary_stats_median_p25_p75() -> None:
    logs = [
        _log(name=f"p{i}", percentile=95.0 + i, setup=_setup(stats={"Haste": float(v)}))
        for i, v in enumerate([1000, 1100, 1200, 1300])
    ]
    result = build_encounter_benchmark(logs, target=TARGET, policy=BenchmarkPolicy.default())
    haste = result.bands["p95-99"].secondary_stats["Haste"]
    assert haste.n == 4
    assert haste.median == pytest.approx(1150.0)
    assert haste.p25 == pytest.approx(1075.0)
    assert haste.p75 == pytest.approx(1225.0)


def test_secondary_stats_always_report_all_four_canonical_keys() -> None:
    logs = [_log(name="a", percentile=96.0, setup=_setup(stats={"Haste": 1000.0}))]
    result = build_encounter_benchmark(logs, target=TARGET, policy=BenchmarkPolicy.default())
    stats = result.bands["p95-99"].secondary_stats
    assert set(stats.keys()) == {"Crit", "Haste", "Mastery", "Versatility"}
    assert stats["Crit"].n == 0
    assert stats["Crit"].median is None  # nunca 0.0 — ausência não é "valor zero"


def test_no_causally_named_fields_anywhere_in_output_types() -> None:
    """28: nenhum campo chamado best_build/optimal_trinket/recommended_stats."""
    import inspect

    from botgitgud.analysis import benchmark_aggregate as module

    source = inspect.getsource(module)
    for forbidden in ("best_build", "optimal", "recommended", "ideal"):
        assert forbidden not in source.lower()


# -- 9/10: duration e ilvl summary --------------------------------------------


def test_duration_summary_covers_every_band_member() -> None:
    logs = [
        _log(name=f"p{i}", percentile=95.0 + i, duration_s=float(v))
        for i, v in enumerate([280.0, 300.0, 320.0, 340.0])
    ]
    result = build_encounter_benchmark(logs, target=TARGET, policy=BenchmarkPolicy.default())
    duration = result.bands["p95-99"].duration_summary
    assert duration.n == 4  # duration_s é sempre presente


def test_item_level_summary_excludes_missing_values() -> None:
    logs = [
        _log(name="a", percentile=96.0, item_level=290.0),
        _log(name="b", percentile=97.0, item_level=None),
    ]
    result = build_encounter_benchmark(logs, target=TARGET, policy=BenchmarkPolicy.default())
    ilvl = result.bands["p95-99"].item_level_summary
    assert ilvl.n == 1  # só "a" tem item_level


# -- 11/12/13: dedup ------------------------------------------------------------


def test_dedup_by_report_code_same_player_different_reports() -> None:
    logs = [
        _log(name="dupe", percentile=95.5, report_code="report-A", setup=_setup(GOOD_BUILD)),
        _log(name="dupe", percentile=96.5, report_code="report-B", setup=_setup(GOOD_BUILD)),
    ]
    result = build_encounter_benchmark(logs, target=TARGET, policy=BenchmarkPolicy.default())
    band = result.bands["p95-99"]
    assert band.sample_size == 1
    assert band.raw_observation_count == 2
    assert result.deduped_count == 1


def test_dedup_by_player_identity_case_and_whitespace_insensitive() -> None:
    logs = [
        _log(name="Wargyu", percentile=95.5, setup=_setup(GOOD_BUILD)),
        _log(name=" wargyu ", percentile=96.5, setup=_setup(GOOD_BUILD)),
    ]
    result = build_encounter_benchmark(logs, target=TARGET, policy=BenchmarkPolicy.default())
    assert result.bands["p95-99"].sample_size == 1


def test_dedup_keeps_the_observation_with_the_highest_rank_percent() -> None:
    logs = [
        _log(name="dupe", percentile=95.5, report_code="report-low", item_level=280.0),
        _log(name="dupe", percentile=98.5, report_code="report-high", item_level=290.0),
    ]
    result = build_encounter_benchmark(logs, target=TARGET, policy=BenchmarkPolicy.default())
    ilvl = result.bands["p95-99"].item_level_summary
    assert ilvl.median == 290.0  # a observação de report-high venceu


def test_dedup_tie_break_uses_report_code_then_fight_id_never_arrival_order() -> None:
    """Mesmo rank_percent, mesmo jogador: o report_code lexicograficamente
    menor vence, seja qual for a ordem de entrada.
    """
    a = _log(name="dupe", percentile=96.0, report_code="report-Z", item_level=280.0)
    b = _log(name="dupe", percentile=96.0, report_code="report-A", item_level=290.0)

    forward = build_encounter_benchmark([a, b], target=TARGET, policy=BenchmarkPolicy.default())
    backward = build_encounter_benchmark([b, a], target=TARGET, policy=BenchmarkPolicy.default())
    assert forward.bands["p95-99"].item_level_summary.median == 290.0
    assert backward.bands["p95-99"].item_level_summary.median == 290.0


# -- 14: independência da ordem de input ---------------------------------------


def test_result_is_independent_of_input_order() -> None:
    logs = _population_50()
    shuffled = list(logs)
    random.Random(42).shuffle(shuffled)

    a = build_encounter_benchmark(logs, target=TARGET, policy=BenchmarkPolicy.default())
    b = build_encounter_benchmark(shuffled, target=TARGET, policy=BenchmarkPolicy.default())
    assert a == b


def test_result_is_independent_of_setup_stats_dict_construction_order() -> None:
    forward = _setup(stats={"Haste": 1.0, "Crit": 2.0})
    backward = _setup(stats={"Crit": 2.0, "Haste": 1.0})
    a = build_encounter_benchmark(
        [_log(name="a", percentile=96.0, setup=forward)],
        target=TARGET,
        policy=BenchmarkPolicy.default(),
    )
    b = build_encounter_benchmark(
        [_log(name="a", percentile=96.0, setup=backward)],
        target=TARGET,
        policy=BenchmarkPolicy.default(),
    )
    assert a == b


# -- 15/16/17: setup ausente, cobertura, denominadores -------------------------


def test_setup_none_is_a_valid_observation_not_an_exception() -> None:
    logs = [_log(name="a", percentile=96.0, setup=None)]
    result = build_encounter_benchmark(logs, target=TARGET, policy=BenchmarkPolicy.default())
    assert result.total_input_observations == 1
    assert result.missing_setup_count == 1
    assert result.bands["p95-99"].sample_size == 1  # a observação continua contando


def test_coverage_ratio_is_correct() -> None:
    logs = [
        _log(name="a", percentile=96.0, setup=_setup(GOOD_BUILD)),
        _log(name="b", percentile=97.0, setup=None),
    ]
    result = build_encounter_benchmark(logs, target=TARGET, policy=BenchmarkPolicy.default())
    assert result.coverage.total_observations == 2
    assert result.coverage.setup_available == 1
    assert result.coverage.setup_missing == 1
    assert result.coverage.coverage_ratio == pytest.approx(0.5)
    assert result.missing_setup_count == result.coverage.setup_missing


def test_talent_denominator_excludes_missing_setup_never_uses_band_total() -> None:
    """Denominador correto: 1 observação SEM setup não deve diluir a
    prevalência de quem TEM setup.
    """
    logs = [
        _log(name="a", percentile=96.0, setup=_setup(GOOD_BUILD)),
        _log(name="b", percentile=97.0, setup=None),
    ]
    result = build_encounter_benchmark(logs, target=TARGET, policy=BenchmarkPolicy.default())
    band = result.bands["p95-99"]
    assert band.sample_size == 2
    assert band.talent_build_prevalence.n_available == 1  # não 2
    assert band.talent_build_prevalence.entries[0].prevalence == 1.0  # não 0.5


# -- 18/19: min_sample_size / insufficient -------------------------------------


def test_band_below_min_sample_size_is_marked_insufficient() -> None:
    logs = [_log(name="a", percentile=96.0)]
    policy = BenchmarkPolicy(min_sample_size=8)
    result = build_encounter_benchmark(logs, target=TARGET, policy=policy)
    assert result.bands["p95-99"].status == "insufficient"


def test_band_at_or_above_min_sample_size_is_ok() -> None:
    logs = [_log(name=f"p{i}", percentile=95.0 + i * 0.1) for i in range(8)]
    policy = BenchmarkPolicy(min_sample_size=8)
    result = build_encounter_benchmark(logs, target=TARGET, policy=policy)
    assert result.bands["p95-99"].status == "ok"


def test_insufficient_band_data_is_not_silently_discarded() -> None:
    logs = [_log(name="a", percentile=96.0, setup=_setup(GOOD_BUILD))]
    result = build_encounter_benchmark(
        logs, target=TARGET, policy=BenchmarkPolicy(min_sample_size=100)
    )
    band = result.bands["p95-99"]
    assert band.status == "insufficient"
    assert band.sample_size == 1  # o dado continua lá, só marcado


# -- 20-25: bandas fora da policy e fronteiras ----------------------------------


@pytest.mark.parametrize(
    ("percentile", "expected_band", "expected_outside"),
    [
        (50.0, "p50-75", False),
        (75.0, "p75-95", False),
        (95.0, "p95-99", False),
        (99.0, None, True),  # fora de toda banda aprovada
        (100.0, None, True),
        (0.0, None, True),
    ],
)
def test_boundary_percentiles_route_correctly(
    percentile: float, expected_band: str | None, expected_outside: bool
) -> None:
    logs = [_log(name="a", percentile=percentile)]
    result = build_encounter_benchmark(logs, target=TARGET, policy=BenchmarkPolicy.default())
    if expected_band is not None:
        assert result.bands[expected_band].sample_size == 1
        assert result.outside_policy_bands == 0
    else:
        assert result.outside_policy_bands == 1
        assert all(b.sample_size == 0 for b in result.bands.values())
    assert expected_outside == (result.outside_policy_bands == 1)


def test_out_of_range_percentile_is_ineligible_not_outside_band() -> None:
    """rank_percent inválido (fora de 0..100) não é elegível de forma
    alguma — diferente de 99/100, que são elegíveis mas fora de banda.
    """
    logs = [_log(name="a", percentile=150.0)]
    result = build_encounter_benchmark(logs, target=TARGET, policy=BenchmarkPolicy.default())
    assert result.eligible_observations == 0
    assert result.outside_policy_bands == 0
    assert result.total_input_observations == 1


def test_missing_percentile_is_ineligible() -> None:
    logs = [_log(name="a", percentile=None)]
    result = build_encounter_benchmark(logs, target=TARGET, policy=BenchmarkPolicy.default())
    assert result.eligible_observations == 0
    assert result.total_input_observations == 1


# -- 26/27: policy customizada -------------------------------------------------


def test_custom_policy_works_without_changing_the_aggregator() -> None:
    custom = BenchmarkPolicy(
        policy_version="v-custom",
        bands=(PercentileBand("top10", 90.0, 100.0), PercentileBand("rest", 0.0, 90.0)),
        min_sample_size=1,
    )
    logs = [_log(name="a", percentile=95.0, setup=_setup(GOOD_BUILD))]
    result = build_encounter_benchmark(logs, target=TARGET, policy=custom)
    assert set(result.bands.keys()) == {"top10", "rest"}
    assert result.bands["top10"].sample_size == 1


def test_policy_version_is_preserved_in_the_result() -> None:
    custom = BenchmarkPolicy(policy_version="v7", min_sample_size=1)
    result = build_encounter_benchmark([], target=TARGET, policy=custom)
    assert result.policy_version == "v7"


def test_hardcoded_95_75_50_are_not_used_when_a_custom_policy_is_supplied() -> None:
    custom = BenchmarkPolicy(
        policy_version="v-custom", bands=(PercentileBand("only", 10.0, 20.0),), min_sample_size=1
    )
    logs = [_log(name="a", percentile=15.0)]
    result = build_encounter_benchmark(logs, target=TARGET, policy=custom)
    assert result.bands["only"].sample_size == 1
    assert result.outside_policy_bands == 0


# -- validação: observação fora do target --------------------------------------


def test_observation_outside_target_dimensions_is_rejected() -> None:
    mismatched = _log(name="a", percentile=96.0)
    from dataclasses import replace

    wrong_encounter = replace(mismatched, fight=replace(mismatched.fight, encounter_id=9999))
    with pytest.raises(EncounterBenchmarkAggregationError):
        build_encounter_benchmark(
            [wrong_encounter], target=TARGET, policy=BenchmarkPolicy.default()
        )


# -- 29/30/31: zero I/O ----------------------------------------------------------


def test_module_imports_no_io_or_network_dependency() -> None:
    import inspect

    from botgitgud.analysis import benchmark_aggregate as module

    source = inspect.getsource(module)
    for forbidden in ("import aiohttp", "import discord", "open(", "duckdb", "requests."):
        assert forbidden not in source


def test_empty_input_produces_an_empty_but_well_formed_benchmark() -> None:
    result = build_encounter_benchmark([], target=TARGET, policy=BenchmarkPolicy.default())
    assert isinstance(result, EncounterBenchmark)
    assert result.total_input_observations == 0
    assert result.coverage.coverage_ratio == 0.0
    assert all(b.sample_size == 0 for b in result.bands.values())
    assert all(b.status == "insufficient" for b in result.bands.values())
