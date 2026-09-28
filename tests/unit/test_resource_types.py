from __future__ import annotations

from botgitgud.domain.resource_types import (
    POWER_TYPE_LABELS,
    resource_type_from_label,
    resource_type_label,
)


def test_known_power_types_get_real_labels() -> None:
    assert resource_type_label(0) == "Mana"
    assert resource_type_label(7) == "Fragmentos de Alma"  # live-verified, Zarad/Demonology


def test_unknown_power_type_falls_back_to_a_numbered_label() -> None:
    assert resource_type_label(999) == "recurso #999"


# -- M3.1 (D-M31-05) / independent review R6 -----------------------------------


def test_resource_type_from_label_inverts_every_known_label() -> None:
    for rtype, label in POWER_TYPE_LABELS.items():
        assert resource_type_from_label(label) == rtype


def test_resource_type_from_label_recovers_the_legacy_numbered_fallback() -> None:
    assert resource_type_from_label("recurso #16") == 16
    assert resource_type_from_label("recurso #0") == 0


def test_resource_type_from_label_unmappable_label_is_none() -> None:
    assert resource_type_from_label("nonsense") is None
    assert resource_type_from_label("recurso #") is None
    assert resource_type_from_label("recurso #x") is None
    assert resource_type_from_label("") is None


def test_resource_type_from_label_round_trips_through_resource_type_label() -> None:
    # For every type resource_type_label can produce a label for (known or
    # falling back to "recurso #<n>"), the reverse must recover the exact
    # original integer — the identity D-M31-05 requires, independent of
    # whatever availability status a caller later attaches to it.
    for rtype in (*POWER_TYPE_LABELS, 16, 999, 12345):
        assert resource_type_from_label(resource_type_label(rtype)) == rtype
