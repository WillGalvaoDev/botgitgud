from __future__ import annotations

from botgitgud.domain.resource_types import resource_type_label


def test_known_power_types_get_real_labels() -> None:
    assert resource_type_label(0) == "Mana"
    assert resource_type_label(7) == "Fragmentos de Alma"  # live-verified, Zarad/Demonology


def test_unknown_power_type_falls_back_to_a_numbered_label() -> None:
    assert resource_type_label(999) == "recurso #999"
