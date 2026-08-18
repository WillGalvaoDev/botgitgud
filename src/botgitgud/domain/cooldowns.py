"""T2.5 — curated base-cooldown table (docs/implementacao.md T2.5).

**Source 1 (Blizzard's spell API), verified live and confirmed
unavailable**: `GET /data/wow/spell/{id}` returns only `id, name,
description, media` — no cooldown field, checked against several real
spell IDs (104316, 1122, 267171). **WCL's own `gameData.ability(id)`
(GraphQL type `GameAbility`) was checked too** (not part of the task's
own list, but the obvious second place to look before giving up on an
API source) — it exposes only `id, icon, name`, same gap. Neither API
this project talks to exposes ability cooldowns.

**Source 2, this table**: starts EMPTY, deliberately. Every other curated
table in this project (domain/blacklist.py, domain/external_buffs.py) was
built by checking real, live data first. There is no equivalent live
check for "what is this spell's base cooldown" — it would mean asserting
specific numeric values from memory, for spell IDs from a very recent
content patch this session has no verified source for. This project's own
standard, applied consistently since T0.1, is "melhor não opinar que
opinar errado" (T2.3's own suppression rule, in spirit) — a wrong
cooldown silently corrupts MAJOR/MINOR classification, which is worse
than the honest "unknown" this table's absence already produces.

**Source 3** (analysis/cadence.py's observed-interval-median / usage-count
fallback) already handles every spell today and is fully exercised in
production — this task's "não bloqueie a Fase 2... é incremental" applies
literally: the mechanism below is complete and tested: add a `{spell_id:
base_cooldown_s}` entry from a trustworthy, verifiable source (an in-game
tooltip, an official patch note, or a future API that exposes this) and
`get_base_cooldown` — already wired into analysis/profile.py and
analysis/comparison.py — makes it take priority immediately, no other
code change needed.
"""

from __future__ import annotations

BASE_COOLDOWNS_S: dict[int, float] = {}


def get_base_cooldown(spell_id: int) -> float | None:
    return BASE_COOLDOWNS_S.get(spell_id)
