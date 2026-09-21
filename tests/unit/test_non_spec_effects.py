from __future__ import annotations

from botgitgud.domain.ability_role import AbilityRole
from botgitgud.domain.non_spec_effects import NON_SPEC_EFFECT_ROLES


def test_curated_non_spec_effect_roles_are_exact() -> None:
    assert dict(NON_SPEC_EFFECT_ROLES) == {
        1236616: AbilityRole.CONSUMABLE,
        1236994: AbilityRole.CONSUMABLE,
        1234768: AbilityRole.CONSUMABLE,
        1295247: AbilityRole.CONSUMABLE,
        6262: AbilityRole.CONSUMABLE,
        452930: AbilityRole.CONSUMABLE,
        1250508: AbilityRole.EQUIPMENT_EFFECT,
        33702: AbilityRole.RACIAL,
        26297: AbilityRole.RACIAL,
        274738: AbilityRole.RACIAL,
        # GATE-M5 lote 1 pos-M18
        1253050: AbilityRole.EQUIPMENT_EFFECT,
        1260459: AbilityRole.EQUIPMENT_EFFECT,
        229837: AbilityRole.EQUIPMENT_EFFECT,
        440837: AbilityRole.EQUIPMENT_EFFECT,
        176890: AbilityRole.EQUIPMENT_EFFECT,
        69070: AbilityRole.RACIAL,
        68992: AbilityRole.RACIAL,
        # GATE-M5 lote 2 pos-M18
        1297761: AbilityRole.EQUIPMENT_EFFECT,
        1293316: AbilityRole.EQUIPMENT_EFFECT,
        395705: AbilityRole.EQUIPMENT_EFFECT,
        335099: AbilityRole.EQUIPMENT_EFFECT,
        440836: AbilityRole.EQUIPMENT_EFFECT,
        1293311: AbilityRole.EQUIPMENT_EFFECT,
        1295735: AbilityRole.EQUIPMENT_EFFECT,
        1293326: AbilityRole.EQUIPMENT_EFFECT,
        383781: AbilityRole.EQUIPMENT_EFFECT,
        1295132: AbilityRole.CONSUMABLE,
        20572: AbilityRole.RACIAL,
        33697: AbilityRole.RACIAL,
        256948: AbilityRole.RACIAL,
        257040: AbilityRole.RACIAL,
        58984: AbilityRole.RACIAL,
        59752: AbilityRole.RACIAL,
        20589: AbilityRole.RACIAL,
        # GATE-M5 lote 3
        1262857: AbilityRole.CONSUMABLE,
        1250533: AbilityRole.EQUIPMENT_EFFECT,
        # GATE-M5 lote 4
        1259633: AbilityRole.EQUIPMENT_EFFECT,
        25046: AbilityRole.RACIAL,
        155145: AbilityRole.RACIAL,
        202719: AbilityRole.RACIAL,
        # GATE-M5-TARGETED-REOPEN
        409632: AbilityRole.EXTERNAL_OFFENSIVE,
        404908: AbilityRole.EXTERNAL_OFFENSIVE,
        410265: AbilityRole.EXTERNAL_OFFENSIVE,
        410263: AbilityRole.EXTERNAL_NON_OFFENSIVE,
    }


def test_cross_class_id_outside_curated_table_is_not_inferred() -> None:
    assert 358733 not in NON_SPEC_EFFECT_ROLES


def test_every_blood_fury_variant_is_curated() -> None:
    """O corpus tem tres IDs chamados Blood Fury; o lote 1 so cobria um deles."""
    assert {NON_SPEC_EFFECT_ROLES[sid] for sid in (20572, 33697, 33702)} == {AbilityRole.RACIAL}


def test_every_arcane_torrent_variant_is_curated() -> None:
    """Tres ids, um por recurso de classe; nenhum pode ficar de fora."""
    assert {NON_SPEC_EFFECT_ROLES[sid] for sid in (25046, 155145, 202719)} == {AbilityRole.RACIAL}
