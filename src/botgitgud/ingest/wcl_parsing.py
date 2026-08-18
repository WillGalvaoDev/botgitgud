"""T1.6 — pure parsing of raw WCL GraphQL JSON into domain-model pieces.

Split out of LogFetcher._fetch_from_api (T1.4) so ingest/log_fetcher.py
stays under the 300-line limit (docs/implementacao.md T1.6), and so this
parsing logic is unit-testable without any network mocking.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from botgitgud.domain.spells import SpellCatalog

_ROLE_GROUP_TO_ROLE = {"dps": "dps", "healers": "healer", "tanks": "tank"}


@dataclass(frozen=True, slots=True)
class PlayerMatch:
    player_id: int
    class_name: str
    spec_name: str
    server: str | None
    region: str | None
    role: str
    item_level: float | None


def find_player_in_details(player_details: dict[str, Any], player: str) -> PlayerMatch | None:
    """Searches every role group (dps/healers/tanks) for a case-insensitive
    name match, same as legacy — the tool rejects unsupported roles later,
    at the scope gate, not here.

    Defensive against malformed shapes: live rankings occasionally point at
    a report/fight whose `table(dataType: Summary)` JSON scalar has
    `playerDetails` as an empty list instead of the usual
    {dps,healers,tanks: [...]} object (observed against the real API while
    recording T1.6's fixtures, docs/desvios.md D-14) — treated the same as
    "player not found" rather than crashing the whole reference-cohort
    fetch over one malformed log.
    """
    if not isinstance(player_details, dict):
        return None
    for role_group, mapped_role in _ROLE_GROUP_TO_ROLE.items():
        group = player_details.get(role_group, [])
        if not isinstance(group, list):
            continue
        for p in group:
            if not isinstance(p, dict):
                continue
            if p.get("name", "").lower() == player.lower():
                specs = p.get("specs", [])
                spec_name = (
                    specs[0].get("spec")
                    if specs and isinstance(specs[0], dict)
                    else (specs[0] if specs else None)
                )
                return PlayerMatch(
                    player_id=p["id"],
                    class_name=p.get("type") or "Unknown",
                    spec_name=spec_name or "Unknown",
                    server=p.get("server"),
                    region=p.get("region"),
                    role=mapped_role,
                    item_level=p.get("maxItemLevel"),
                )
    return None


def extract_damage_total(damage_done: list[dict[str, Any]], player_id: int) -> float | None:
    for entry in damage_done:
        if entry.get("id") == player_id:
            return entry.get("total")
    return None


def learn_spells_from_casts_table(
    casts_entries: list[dict[str, Any]], player_id: int, catalog: SpellCatalog
) -> None:
    for entry in casts_entries:
        if entry.get("id") != player_id:
            continue
        for ab in entry.get("abilities", []):
            guid = ab.get("guid") or ab.get("id")
            name = ab.get("name")
            if guid and name:
                catalog.learn(int(guid), name, "wcl")


def find_matching_rank_percent(
    character: dict[str, Any] | None, report_code: str, fight_id: int
) -> float | None:
    """The real percentile source (docs/schema_confirmado.md §9):
    characterData.character.encounterRankings.ranks[], matched by this
    exact report+fight — characterRankings (plural, used for cohort
    discovery) has no percentile field at all (achado 3.10).
    """
    if not character:
        return None
    for rank in (character.get("encounterRankings") or {}).get("ranks") or []:
        rep = rank.get("report", {})
        if rep.get("code") == report_code and rep.get("fightID") == fight_id:
            return rank.get("rankPercent")
    return None


def parse_cast_events(
    events: list[dict[str, Any]], player_id: int, start_time_ms: float
) -> dict[int, list[float]]:
    """One page's worth of cast events -> {spell_id: [relative_seconds, ...]}.
    Callers merge across pages (a spell can be cast in more than one page).
    """
    timeline: dict[int, list[float]] = {}
    for ev in events:
        if ev.get("sourceID") != player_id or ev.get("type") != "cast":
            continue
        spell_id = ev.get("abilityGameID") or ev.get("ability")
        if not spell_id:
            continue
        rel_sec = round((ev.get("timestamp", start_time_ms) - start_time_ms) / 1000.0, 1)
        timeline.setdefault(int(spell_id), []).append(rel_sec)
    return timeline
