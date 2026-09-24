"""Static, explicit spell-ID blacklist for major/minor CD detection.

Replaces the original bot's lexical substring filter (achado 3.12 of its
audit), which false-positived on real ability names. Empirical
proof from this project's own tracked spells.json: "Festering Scythe"
(id 458128) and "Festering Strike" (id 85948) — real Death Knight
abilities with nothing to do with rings — both contain the substring
"ring" (fesTERING). The legacy filter would have silently reclassified
them as non-trackable. "Ring of Peace" is the same failure mode from the
other direction. Lexical filtering is banned; every entry here is a spell
ID with a documented reason.

No consumable-item or PvP-accessory-proc spell IDs were found while
building this list from the recorded fixtures (tests/fixtures/cassettes/)
or from the project's spells.json: those are tracked via separate summary
counters on the player record (a "times drunk" / "times used" style
count), not as entries in the casts/events data this catalog resolves
names for. If one is ever observed in practice, add it here with a
reason — never as a substring check.
"""

from __future__ import annotations

MAJOR_CD_BLACKLIST: frozenset[int] = frozenset(
    {
        22812,  # Barkskin — defensive CD, not offensive/utility (legacy MAJOR_CD_BLACKLIST)
    }
)
