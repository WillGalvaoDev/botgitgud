from __future__ import annotations

from typing import Any, NoReturn

import structlog

from botgitgud.domain.specs import SpecId, SpecSupport, classify_spec, rejection_message

_SUPPORTED_SPECS = [
    ("Death Knight", "Frost"),
    ("Death Knight", "Unholy"),
    ("Demon Hunter", "Havoc"),
    ("Demon Hunter", "Devourer"),
    ("Druid", "Balance"),
    ("Druid", "Feral"),
    ("Evoker", "Devastation"),
    ("Hunter", "Beast Mastery"),
    ("Hunter", "Marksmanship"),
    ("Hunter", "Survival"),
    ("Mage", "Arcane"),
    ("Mage", "Fire"),
    ("Mage", "Frost"),
    ("Monk", "Windwalker"),
    ("Paladin", "Retribution"),
    ("Priest", "Shadow"),
    ("Rogue", "Assassination"),
    ("Rogue", "Outlaw"),
    ("Rogue", "Subtlety"),
    ("Shaman", "Elemental"),
    ("Shaman", "Enhancement"),
    ("Warlock", "Affliction"),
    ("Warlock", "Demonology"),
    ("Warlock", "Destruction"),
    ("Warrior", "Arms"),
    ("Warrior", "Fury"),
]


def test_all_supported_specs_classify_as_supported() -> None:
    assert len(_SUPPORTED_SPECS) == 26
    for class_name, spec_name in _SUPPORTED_SPECS:
        result = classify_spec(SpecId(class_name=class_name, spec_name=spec_name))
        assert result == SpecSupport.SUPPORTED, f"{class_name}/{spec_name} deveria ser SUPPORTED"


def test_supported_specs_use_wcl_no_space_convention_too() -> None:
    """Confirmado ao vivo contra a API (docs/schema_confirmado.md): tanto
    characterRankings quanto playerDetails[].type retornam nomes sem
    espaço ('DemonHunter', 'BeastMastery'). O portão precisa aceitar essa
    forma também, não só a legível-para-humanos da allowlist.
    """
    dh = SpecId(class_name="DemonHunter", spec_name="Havoc")
    hunter = SpecId(class_name="Hunter", spec_name="BeastMastery")
    assert classify_spec(dh) == SpecSupport.SUPPORTED
    assert classify_spec(hunter) == SpecSupport.SUPPORTED


def test_devourer_accepts_wcl_readable_and_normalized_forms() -> None:
    variants = (
        SpecId(class_name="DemonHunter", spec_name="Devourer"),
        SpecId(class_name="Demon Hunter", spec_name="Devourer"),
        SpecId(class_name="  dEmOn  HuNtEr  ", spec_name="  dEvOuReR  "),
    )

    assert all(classify_spec(spec) == SpecSupport.SUPPORTED for spec in variants)


def test_demon_hunter_vengeance_remains_out_of_scope_tank() -> None:
    assert (
        classify_spec(SpecId(class_name="DemonHunter", spec_name="Vengeance"))
        == SpecSupport.OUT_OF_SCOPE_TANK
    )


def test_augmentation_evoker_is_out_of_scope_support_with_specific_message() -> None:
    spec = SpecId(class_name="Evoker", spec_name="Augmentation")
    result = classify_spec(spec)
    assert result == SpecSupport.OUT_OF_SCOPE_SUPPORT
    msg = rejection_message(result, spec)
    assert msg is not None
    assert "atribuída a outros" in msg


def test_devastation_evoker_is_supported_not_confused_with_augmentation() -> None:
    assert (
        classify_spec(SpecId(class_name="Evoker", spec_name="Devastation")) == SpecSupport.SUPPORTED
    )


def test_priest_discipline_is_healer() -> None:
    result = classify_spec(SpecId(class_name="Priest", spec_name="Discipline"))
    assert result == SpecSupport.OUT_OF_SCOPE_HEALER
    assert "healers" in (rejection_message(result, SpecId("Priest", "Discipline")) or "")


def test_warrior_protection_is_tank() -> None:
    result = classify_spec(SpecId(class_name="Warrior", spec_name="Protection"))
    assert result == SpecSupport.OUT_OF_SCOPE_TANK
    assert "tanks" in (rejection_message(result, SpecId("Warrior", "Protection")) or "")


def test_unknown_spec_fails_closed_and_logs_warning() -> None:
    events: list[dict[str, object]] = []

    def _capture(_logger: object, _method_name: str, event_dict: Any) -> NoReturn:
        events.append(dict(event_dict))
        raise structlog.DropEvent

    structlog.configure(
        processors=[_capture],
        wrapper_class=structlog.make_filtering_bound_logger(0),
    )
    try:
        result = classify_spec(SpecId(class_name="Mage", spec_name="Chronomancer"))
    finally:
        structlog.reset_defaults()

    assert result == SpecSupport.UNKNOWN
    assert any(e.get("event") == "specs.unknown_spec" for e in events)

    msg = rejection_message(result, SpecId("Mage", "Chronomancer"))
    assert msg is not None
    assert "Mage/Chronomancer" in msg


def test_supported_spec_has_no_rejection_message() -> None:
    spec = SpecId(class_name="Warlock", spec_name="Demonology")
    assert rejection_message(classify_spec(spec), spec) is None


def test_tank_and_healer_and_support_lists_do_not_overlap_supported() -> None:
    from botgitgud.domain.specs import _HEALER_SPECS, _SUPPORT_SPECS, _SUPPORTED, _TANK_SPECS

    assert _SUPPORTED.isdisjoint(_TANK_SPECS)
    assert _SUPPORTED.isdisjoint(_HEALER_SPECS)
    assert _SUPPORTED.isdisjoint(_SUPPORT_SPECS)
    assert _TANK_SPECS.isdisjoint(_HEALER_SPECS)
