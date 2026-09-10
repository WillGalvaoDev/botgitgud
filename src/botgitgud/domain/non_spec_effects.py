"""Human-curated roles for effects that do not belong to a player's spec.

Entries cover both class/spec-agnostic effects and effects causally originating
from another player.  Every key is an exact spell ID; names are comments for
auditability only and are never used for matching.
"""

from __future__ import annotations

from types import MappingProxyType

from botgitgud.domain.ability_role import AbilityRole

NON_SPEC_EFFECT_ROLES = MappingProxyType(
    {
        1236616: AbilityRole.CONSUMABLE,  # Light's Potential
        1236994: AbilityRole.CONSUMABLE,  # Potion of Recklessness
        1234768: AbilityRole.CONSUMABLE,  # Silvermoon Health Potion
        1295247: AbilityRole.CONSUMABLE,  # Concentrated Silvermoon Health Potion
        6262: AbilityRole.CONSUMABLE,  # Healthstone
        452930: AbilityRole.CONSUMABLE,  # Demonic Healthstone
        1250508: AbilityRole.EQUIPMENT_EFFECT,  # Emberwing Heatwave
        33702: AbilityRole.RACIAL,  # Blood Fury
        26297: AbilityRole.RACIAL,  # Berserking
        274738: AbilityRole.RACIAL,  # Ancestral Call
        # GATE-M5 lote 1 pos-M18. Efeitos de item e raciais sao agnosticos de class/spec,
        # entao a chave por spell_id e a correta: uma entrada cobre toda classe que os use.
        1253050: AbilityRole.EQUIPMENT_EFFECT,  # Dawn Crystal
        1260459: AbilityRole.EQUIPMENT_EFFECT,  # Nullsight
        229837: AbilityRole.EQUIPMENT_EFFECT,  # Big Red Rays
        440837: AbilityRole.EQUIPMENT_EFFECT,  # Fury of Xuen
        176890: AbilityRole.EQUIPMENT_EFFECT,  # Winning Hand
        69070: AbilityRole.RACIAL,  # Rocket Jump
        68992: AbilityRole.RACIAL,  # Darkflight
        # GATE-M5 lote 2 pos-M18.
        1297761: AbilityRole.EQUIPMENT_EFFECT,  # Voracious Heart of Ula'tek
        1293316: AbilityRole.EQUIPMENT_EFFECT,  # Empowering Venom
        395705: AbilityRole.EQUIPMENT_EFFECT,  # Assorted Arcanocrystals
        335099: AbilityRole.EQUIPMENT_EFFECT,  # Smoldering
        440836: AbilityRole.EQUIPMENT_EFFECT,  # Essence of Yu'lon
        1293311: AbilityRole.EQUIPMENT_EFFECT,  # Coiled Fangstone
        1295735: AbilityRole.EQUIPMENT_EFFECT,  # Battle Fervor
        1293326: AbilityRole.EQUIPMENT_EFFECT,  # Tattered Amani War Banner
        383781: AbilityRole.EQUIPMENT_EFFECT,  # Algeth'ar Puzzle
        1295132: AbilityRole.CONSUMABLE,  # Liquid Luster
        # Blood Fury tem TRES ids no corpus (variantes de ataque/feitico/ambos);
        # 33702 ja estava na tabela desde o lote 1, faltavam os outros dois.
        20572: AbilityRole.RACIAL,  # Blood Fury
        33697: AbilityRole.RACIAL,  # Blood Fury
        256948: AbilityRole.RACIAL,  # Spatial Rift
        257040: AbilityRole.RACIAL,  # Spatial Rift
        58984: AbilityRole.RACIAL,  # Shadowmeld
        59752: AbilityRole.RACIAL,  # Will to Survive
        20589: AbilityRole.RACIAL,  # Escape Artist
        # GATE-M5 lote 3.
        1262857: AbilityRole.CONSUMABLE,  # Potent Healing Potion
        1250533: AbilityRole.EQUIPMENT_EFFECT,  # Freightrunner's Flask
        # GATE-M5 lote 4.  Arcane Torrent tem tres ids (variantes por recurso de classe).
        1259633: AbilityRole.EQUIPMENT_EFFECT,  # Charge!
        25046: AbilityRole.RACIAL,  # Arcane Torrent
        155145: AbilityRole.RACIAL,  # Arcane Torrent
        202719: AbilityRole.RACIAL,  # Arcane Torrent
        # GATE-M5-TARGETED-REOPEN
        409632: AbilityRole.EXTERNAL_OFFENSIVE,  # Breath of Eons
        404908: AbilityRole.EXTERNAL_OFFENSIVE,  # Fate Mirror
        410265: AbilityRole.EXTERNAL_OFFENSIVE,  # Inferno's Blessing
        410263: AbilityRole.EXTERNAL_NON_OFFENSIVE,  # Inferno's Blessing (aura)
    }
)
