from __future__ import annotations

from botgitgud.analysis.alignment import AlignmentKind
from botgitgud.analysis.comparison import compare_spell_usage
from botgitgud.domain.spells import SpellInfo


def _spell(spell_id: int = 1, name: str = "Test Spell") -> SpellInfo:
    return SpellInfo(spell_id=spell_id, name=name, source="wcl")


def test_zero_user_usage_produces_all_missed_alignment() -> None:
    """achado 3.1 (o segundo bug): legacy pulava spells com user_times vazio.
    A função de comparação em si já suporta isso — o all-MISSED alignment é
    o dado que a T0.7 usa para incluir a habilidade no relatório mesmo assim.
    """
    comparison = compare_spell_usage(
        spell=_spell(),
        presence=0.9,
        user_times=[],
        ref_times=[10.0, 130.0, 250.0, 370.0],
        n_usages_median=4.0,
        reference_n=10,
    )
    assert comparison.alignment.n_missed == 4
    assert comparison.alignment.n_matched == 0
    assert all(s.kind is AlignmentKind.MISSED for s in comparison.alignment.steps)


def test_classification_flows_through_from_cadence() -> None:
    comparison = compare_spell_usage(
        spell=_spell(),
        presence=0.9,
        user_times=[100.0],
        ref_times=[100.0],  # single ref usage, base_cooldown unknown -> MAJOR per T0.6
        n_usages_median=1.0,
        reference_n=5,
    )
    assert comparison.cd_type == "MAJOR"


def test_reference_n_and_presence_are_preserved() -> None:
    comparison = compare_spell_usage(
        spell=_spell(spell_id=42, name="Fireball"),
        presence=0.75,
        user_times=[10.0],
        ref_times=[10.0, 20.0],
        n_usages_median=2.0,
        reference_n=17,
    )
    assert comparison.reference_n == 17
    assert comparison.presence == 0.75
    assert comparison.spell.spell_id == 42


def test_extra_usage_is_represented_without_masking() -> None:
    comparison = compare_spell_usage(
        spell=_spell(),
        presence=0.8,
        user_times=[10.0, 40.0, 70.0],
        ref_times=[10.0, 40.0],
        n_usages_median=2.0,
        reference_n=8,
    )
    assert comparison.alignment.n_extra == 1
    extra_steps = [s for s in comparison.alignment.steps if s.kind is AlignmentKind.EXTRA]
    assert extra_steps[0].delta is None  # never a masked 0.0
