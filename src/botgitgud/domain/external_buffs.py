"""T2.1 — curated spell IDs for the `has_augmentation` and `external_buffs`
covariates (docs/implementacao.md T2.1). Same ID-only philosophy as
domain/blacklist.py: no lexical matching, no attempt at exhaustiveness —
this covariate is explicitly allowed to degrade (be relaxed) in the
matching cascade when it doesn't discriminate cleanly, so a few missing
IDs here cost accuracy, not correctness.
"""

from __future__ import annotations

# docs/schema_confirmado.md §11: confirmed live via gameData.ability(id).
AUGMENTATION_BUFF_IDS: frozenset[int] = frozenset({395152, 410089, 413984})

# A representative (not exhaustive) set of externally-cast raid cooldowns
# — buffs one player casts on another, as opposed to self-buffs. Extend as
# real reports surface more.
EXTERNAL_BUFF_IDS: frozenset[int] = frozenset(
    {
        10060,  # Power Infusion (Priest)
        29166,  # Innervate (Druid)
        102342,  # Ironbark (Druid)
        6940,  # Blessing of Sacrifice (Paladin)
        1022,  # Blessing of Protection (Paladin)
        1044,  # Blessing of Freedom (Paladin)
        33206,  # Pain Suppression (Priest)
        47788,  # Guardian Spirit (Priest)
        97462,  # Rallying Cry (Warrior)
        342246,  # Darkness (Demon Hunter)
    }
)

# Only these external buffs have offensive evidence in the current catalog.
EXTERNAL_OFFENSIVE_IDS: frozenset[int] = frozenset({10060}) | AUGMENTATION_BUFF_IDS
EXTERNAL_NON_OFFENSIVE_IDS: frozenset[int] = (
    EXTERNAL_BUFF_IDS | AUGMENTATION_BUFF_IDS
) - EXTERNAL_OFFENSIVE_IDS
