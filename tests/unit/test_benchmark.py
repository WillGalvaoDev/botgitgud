"""EB.1 — identidade determinística + política versionada do Encounter
Benchmark. 100% offline: nenhum arquivo, nenhuma rede, nenhuma agregação
real (isso é EB.2+).
"""

from __future__ import annotations

import dataclasses

import pytest

from botgitgud.analysis.benchmark import (
    DEFAULT_BANDS,
    DEFAULT_BENCHMARK_POLICY_VERSION,
    BenchmarkPolicy,
    BenchmarkPolicyError,
    DedupPolicy,
    EncounterBenchmarkTarget,
    EncounterBenchmarkTargetError,
    PercentileBand,
)
from botgitgud.domain.specs import SpecId

# -- target: identidade determinística ------------------------------------


def _target(**overrides: object) -> EncounterBenchmarkTarget:
    defaults: dict[str, object] = {
        "spec": SpecId("Warlock", "Demonology"),
        "encounter_id": 3179,
        "difficulty": 5,
        "partition": 3,
        "benchmark_policy_version": "v1",
    }
    defaults.update(overrides)
    return EncounterBenchmarkTarget(**defaults)  # type: ignore[arg-type]


def test_target_is_deterministic() -> None:
    """1. mesmos inputs -> mesmo ID, em duas construções independentes."""
    assert _target().benchmark_id == _target().benchmark_id


def test_target_id_is_path_safe() -> None:
    """2. cada componente já passou por canonicalização alnum/version-safe."""
    benchmark_id = _target().benchmark_id
    for forbidden in ("\\", ":", "*", "?", '"', "<", ">", "|", " "):
        assert forbidden not in benchmark_id
    assert benchmark_id.count("/") == 5


def test_spec_changes_the_id() -> None:
    """3."""
    assert _target().benchmark_id != _target(spec=SpecId("Mage", "Fire")).benchmark_id


def test_encounter_changes_the_id() -> None:
    """4."""
    assert _target().benchmark_id != _target(encounter_id=3130).benchmark_id


def test_difficulty_changes_the_id() -> None:
    """5."""
    assert _target().benchmark_id != _target(difficulty=4).benchmark_id


def test_partition_changes_the_id() -> None:
    """6."""
    assert _target().benchmark_id != _target(partition=4).benchmark_id


def test_policy_version_changes_the_id() -> None:
    """7. condição obrigatória da revisão: mudar a versão da policy nunca
    reaproveita silenciosamente um benchmark anterior.
    """
    assert (
        _target(benchmark_policy_version="v1").benchmark_id
        != _target(benchmark_policy_version="v2").benchmark_id
    )


def test_duration_is_not_a_field_of_the_identity() -> None:
    """8."""
    field_names = {f.name for f in dataclasses.fields(EncounterBenchmarkTarget)}
    assert "duration" not in field_names
    assert "duration_s" not in field_names


def test_item_level_is_not_a_field_of_the_identity() -> None:
    """9."""
    field_names = {f.name for f in dataclasses.fields(EncounterBenchmarkTarget)}
    assert "item_level" not in field_names
    assert "ilvl" not in field_names


# -- target: validação (falha fechado) -------------------------------------


def test_invalid_encounter_id_is_rejected() -> None:
    with pytest.raises(EncounterBenchmarkTargetError):
        _target(encounter_id=0)
    with pytest.raises(EncounterBenchmarkTargetError):
        _target(encounter_id=-1)


def test_invalid_difficulty_is_rejected() -> None:
    with pytest.raises(EncounterBenchmarkTargetError):
        _target(difficulty=0)


def test_invalid_partition_is_rejected() -> None:
    with pytest.raises(EncounterBenchmarkTargetError):
        _target(partition=-1)


def test_partition_zero_is_allowed() -> None:
    """0 é um valor real de partition (mesma regra de Phase4Target)."""
    assert _target(partition=0).partition == 0


def test_invalid_policy_version_is_rejected() -> None:
    with pytest.raises(EncounterBenchmarkTargetError):
        _target(benchmark_policy_version="")
    with pytest.raises(EncounterBenchmarkTargetError):
        _target(benchmark_policy_version="has spaces")
    with pytest.raises(EncounterBenchmarkTargetError):
        _target(benchmark_policy_version="has/slash")


def test_class_and_spec_names_are_canonicalized() -> None:
    target = _target(spec=SpecId(" Demon Hunter ", "Havoc"))
    assert target.spec.class_name == "DemonHunter"


def test_invalid_spec_name_is_rejected() -> None:
    with pytest.raises(EncounterBenchmarkTargetError):
        _target(spec=SpecId("Warlock", "Demon/ology"))


# -- target: serialização e round-trip --------------------------------------


def test_target_round_trips_through_to_dict_from_dict() -> None:
    """16/17."""
    original = _target()
    restored = EncounterBenchmarkTarget.from_dict(original.to_dict())
    assert restored == original
    assert restored.benchmark_id == original.benchmark_id


def test_target_round_trips_through_benchmark_id_parse() -> None:
    original = _target()
    restored = EncounterBenchmarkTarget.parse(original.benchmark_id)
    assert restored == original


def test_parse_rejects_malformed_id() -> None:
    with pytest.raises(EncounterBenchmarkTargetError):
        EncounterBenchmarkTarget.parse("too/few/parts")
    with pytest.raises(EncounterBenchmarkTargetError):
        EncounterBenchmarkTarget.parse("Warlock/Demonology/x/5/3/v1")


def test_construction_order_of_kwargs_does_not_affect_the_id() -> None:
    """18 (metade do target): kwargs em ordem diferente, mesmo objeto."""
    a = EncounterBenchmarkTarget(
        spec=SpecId("Warlock", "Demonology"),
        encounter_id=3179,
        difficulty=5,
        partition=3,
    )
    b = EncounterBenchmarkTarget(
        partition=3,
        difficulty=5,
        encounter_id=3179,
        spec=SpecId("Warlock", "Demonology"),
    )
    assert a.benchmark_id == b.benchmark_id


def test_changing_policy_body_without_bumping_version_does_not_change_target_id() -> None:
    """Invariante arquitetural central: a identidade do target depende só
    da STRING de versão, nunca do corpo inteiro da policy — então trocar
    bandas/min_sample_size internos de uma policy, mantendo a mesma
    versão, não invalida silenciosamente nenhum target já calculado.
    """
    permissive = BenchmarkPolicy(policy_version="v1", min_sample_size=1)
    strict = BenchmarkPolicy(policy_version="v1", min_sample_size=50)
    assert permissive.policy_version == strict.policy_version
    a = _target(benchmark_policy_version=permissive.policy_version)
    b = _target(benchmark_policy_version=strict.policy_version)
    assert a.benchmark_id == b.benchmark_id


# -- policy: bandas padrão ---------------------------------------------------


def test_default_policy_has_new_top_band_and_preserves_old_bands() -> None:
    """10."""
    policy = BenchmarkPolicy.default()
    assert [b.name for b in policy.bands] == ["p50-75", "p75-95", "p95-99", "p99-100"]
    assert policy.policy_version == DEFAULT_BENCHMARK_POLICY_VERSION == "v2"
    by_name = {b.name: (b.low, b.high) for b in DEFAULT_BANDS}
    assert by_name == {
        "p99-100": (99.0, 100.0),
        "p95-99": (95.0, 99.0),
        "p75-95": (75.0, 95.0),
        "p50-75": (50.0, 75.0),
    }


# -- policy: fronteiras (achado central do ticket) --------------------------


@pytest.mark.parametrize(
    ("percentile", "expected_band"),
    [
        (0.0, None),  # abaixo de qualquer banda aprovada
        (49.999, None),
        (50.0, "p50-75"),  # low inclusivo
        (74.999, "p50-75"),
        (75.0, "p75-95"),  # a fronteira que o ticket citou como exemplo de ambiguidade
        (94.999, "p75-95"),
        (95.0, "p95-99"),  # idem: 95 entra em p95-99, não em p75-95
        (98.999, "p95-99"),
        (99.0, "p99-100"),
        (100.0, "p99-100"),  # topo do domínio é inclusivo
    ],
)
def test_band_boundaries_are_unambiguous(percentile: float, expected_band: str | None) -> None:
    """11."""
    policy = BenchmarkPolicy.default()
    band = policy.band_for(percentile)
    assert (band.name if band is not None else None) == expected_band


def test_percentile_none_returns_no_band_without_raising() -> None:
    assert BenchmarkPolicy.default().band_for(None) is None


@pytest.mark.parametrize("percentile", [-0.001, 100.001, 150.0, -50.0])
def test_percentile_out_of_range_fails_closed(percentile: float) -> None:
    with pytest.raises(BenchmarkPolicyError):
        BenchmarkPolicy.default().band_for(percentile)


# -- policy: validação de bandas ---------------------------------------------


def test_bands_are_not_allowed_to_overlap() -> None:
    """12."""
    with pytest.raises(BenchmarkPolicyError):
        BenchmarkPolicy(bands=(PercentileBand("a", 50.0, 80.0), PercentileBand("b", 75.0, 95.0)))


def test_contiguous_bands_touching_at_the_boundary_are_allowed() -> None:
    """Fronteira compartilhada (high de uma == low da próxima) não é
    sobreposição — é exatamente o que a policy padrão faz em 75 e 95.
    """
    BenchmarkPolicy(bands=(PercentileBand("a", 50.0, 75.0), PercentileBand("b", 75.0, 95.0)))


def test_bands_are_canonicalized_regardless_of_construction_order() -> None:
    """13."""
    reversed_order = BenchmarkPolicy(bands=tuple(reversed(DEFAULT_BANDS)))
    assert [b.name for b in reversed_order.bands] == [
        "p50-75",
        "p75-95",
        "p95-99",
        "p99-100",
    ]


def test_invalid_band_is_rejected_at_construction() -> None:
    """14. banda invertida (low >= high) falha imediatamente, na própria
    PercentileBand, antes mesmo de chegar numa policy.
    """
    with pytest.raises(BenchmarkPolicyError):
        PercentileBand("inverted", 80.0, 50.0)
    with pytest.raises(BenchmarkPolicyError):
        PercentileBand("out-of-range", -1.0, 50.0)
    with pytest.raises(BenchmarkPolicyError):
        PercentileBand("out-of-range", 50.0, 101.0)
    with pytest.raises(BenchmarkPolicyError):
        PercentileBand("", 50.0, 75.0)


def test_duplicate_band_name_is_rejected() -> None:
    """15."""
    with pytest.raises(BenchmarkPolicyError):
        BenchmarkPolicy(
            bands=(PercentileBand("dup", 50.0, 60.0), PercentileBand("dup", 70.0, 80.0))
        )


def test_policy_without_bands_is_rejected() -> None:
    with pytest.raises(BenchmarkPolicyError):
        BenchmarkPolicy(bands=())


def test_invalid_policy_version_is_rejected_on_policy_too() -> None:
    with pytest.raises(BenchmarkPolicyError):
        BenchmarkPolicy(policy_version="")


def test_min_sample_size_must_be_at_least_one() -> None:
    with pytest.raises(BenchmarkPolicyError):
        BenchmarkPolicy(min_sample_size=0)


def test_max_data_age_days_must_be_positive_when_set() -> None:
    with pytest.raises(BenchmarkPolicyError):
        BenchmarkPolicy(max_data_age_days=0)
    with pytest.raises(BenchmarkPolicyError):
        BenchmarkPolicy(max_data_age_days=-1.0)
    BenchmarkPolicy(max_data_age_days=30.0)  # valor positivo é aceito


def test_dedup_policy_is_declarative_only_no_aggregation_here() -> None:
    policy = BenchmarkPolicy(dedup_policy=DedupPolicy.NONE)
    assert policy.dedup_policy is DedupPolicy.NONE
    assert BenchmarkPolicy.default().dedup_policy is DedupPolicy.ONE_LOG_PER_PLAYER


# -- policy: serialização e round-trip ---------------------------------------


def test_policy_round_trips_through_to_dict_from_dict() -> None:
    """16/17."""
    original = BenchmarkPolicy(
        policy_version="v2",
        bands=(PercentileBand("top", 90.0, 100.0), PercentileBand("rest", 0.0, 90.0)),
        min_sample_size=12,
        dedup_policy=DedupPolicy.NONE,
        max_data_age_days=45.0,
    )
    restored = BenchmarkPolicy.from_dict(original.to_dict())
    assert restored == original


def test_percentile_band_round_trips_through_to_dict_from_dict() -> None:
    original = PercentileBand("p95-99", 95.0, 99.0)
    assert PercentileBand.from_dict(original.to_dict()) == original


def test_policy_serialization_does_not_depend_on_band_construction_order() -> None:
    """18 (metade da policy)."""
    a = BenchmarkPolicy(bands=DEFAULT_BANDS)
    b = BenchmarkPolicy(bands=tuple(reversed(DEFAULT_BANDS)))
    assert a.to_dict() == b.to_dict()


# -- determinismo real, não apenas via ==  -----------------------------------


def test_id_is_stable_across_many_independent_constructions() -> None:
    """19: nenhuma dependência de hash()/randomização de processo — reconstruir
    o mesmo target 50 vezes por caminhos diferentes (kwargs, to_dict/from_dict,
    parse) produz sempre o mesmo ID.
    """
    ids = set()
    for _ in range(50):
        ids.add(_target().benchmark_id)
        ids.add(EncounterBenchmarkTarget.from_dict(_target().to_dict()).benchmark_id)
        ids.add(EncounterBenchmarkTarget.parse(_target().benchmark_id).benchmark_id)
    assert len(ids) == 1


def test_module_never_relies_on_pythons_object_hash_for_identity() -> None:
    """Guarda estática: `hash()`/dict-ordering acidental nunca deve entrar
    no cálculo do benchmark_id — a serialização usa sempre estruturas
    explicitamente ordenadas (mesmo estilo de guard de
    tests/unit/test_cadence.py).
    """
    import inspect

    from botgitgud.analysis import benchmark as benchmark_module

    source = inspect.getsource(benchmark_module)
    assert "hash(" not in source


# -- zero I/O / zero rede -----------------------------------------------------


def test_module_imports_no_io_or_network_dependency() -> None:
    """20/21/22: o módulo inteiro não importa nada de rede, disco ou Discord."""
    import inspect

    from botgitgud.analysis import benchmark as benchmark_module

    source = inspect.getsource(benchmark_module)
    for forbidden in ("import aiohttp", "import discord", "open(", "Path(", "duckdb"):
        assert forbidden not in source
