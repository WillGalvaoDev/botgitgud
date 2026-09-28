"""T3.1 — pure parsing for the performance features beyond casts (active
time, uptimes, deaths/downtime, pet-aware damage, resource waste). Split
out of ingest/wcl_parsing.py (T1.6's own 300-line limit still applies) —
this module is exactly the T3.1-shaped growth of that one.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from botgitgud.domain.models import (
    AuraBand,
    AuraTableProvenance,
    CollectionProvenance,
    CollectionStatus,
)


@dataclass(frozen=True, slots=True)
class AuraUptime:
    spell_id: int
    name: str
    uptime_frac: float
    total_uses: int = 0
    bands: tuple[AuraBand, ...] = ()


def parse_aura_uptimes(buffs_data: dict[str, Any]) -> list[AuraUptime]:
    """Shared by the Buffs and Debuffs tables (identical shape,
    docs/schema_confirmado.md §6) — `totalUptime` (ms) / `totalTime` (ms)
    is each aura's uptime fraction for this fight.
    """
    if not isinstance(buffs_data, dict):
        return []
    auras = buffs_data.get("auras")
    total_time = buffs_data.get("totalTime")
    if not isinstance(auras, list) or not total_time:
        return []
    result = []
    for a in auras:
        if not isinstance(a, dict) or "guid" not in a:
            continue
        guid = int(a["guid"])
        uptime_ms = a.get("totalUptime")
        if uptime_ms is None:
            continue
        result.append(
            AuraUptime(
                spell_id=guid,
                name=a.get("name") or f"Spell #{guid}",
                uptime_frac=uptime_ms / total_time,
                total_uses=int(a.get("totalUses") or 0),
                bands=tuple(
                    AuraBand(start_ms=int(b["startTime"]), end_ms=int(b["endTime"]))
                    for b in (a.get("bands") or [])
                    if isinstance(b, dict) and "startTime" in b and "endTime" in b
                ),
            )
        )
    return result


def parse_death_events(death_events: list[dict[str, Any]], player_id: int) -> list[float]:
    """This player's own death timestamps, in ms **relative to fight
    start** — unlike phaseTransitions[].startTime (absolute report time,
    docs/schema_confirmado.md §7), Summary.deathEvents[].deathTime is
    fight-relative (live-verified: values fall inside [0, duration_ms]).
    """
    return [
        float(e["deathTime"])
        for e in death_events
        if isinstance(e, dict) and e.get("id") == player_id and "deathTime" in e
    ]


def compute_downtime_s(
    death_times_ms: Sequence[float], all_cast_times_s: Sequence[float], duration_s: float
) -> float:
    """Sum, over every death, of (this player's next own cast, or fight
    end, whichever comes first) minus the death time. No WCL API exposes a
    revive timestamp (same gap as docs/architecture.md D-28's missing cooldown
    data) — but a dead character cannot cast, so their own next cast is
    the earliest observable proof they were back up. A player who dies and
    never casts again (e.g. a wipe) correctly gets downtime running to
    fight end, not a fabricated revive.
    """
    total = 0.0
    sorted_casts = sorted(all_cast_times_s)
    for death_ms in death_times_ms:
        death_s = death_ms / 1000.0
        next_cast = next((t for t in sorted_casts if t > death_s), None)
        end = duration_s if next_cast is None else min(next_cast, duration_s)
        total += max(0.0, end - death_s)
    return total


def find_damage_table_entry(entries: list[dict[str, Any]], player_id: int) -> dict[str, Any] | None:
    for e in entries:
        if isinstance(e, dict) and e.get("id") == player_id:
            return e
    return None


def compute_active_time_pct(entry: dict[str, Any] | None, duration_ms: float) -> float | None:
    """docs/schema_confirmado.md §5.5: `entry.activeTime` (ms) is available
    directly on the DamageDone table, no extra query. None (not 0.0) when
    the player's entry or its activeTime is missing — never fabricated.
    """
    if entry is None or duration_ms <= 0:
        return None
    active_ms = entry.get("activeTime")
    if active_ms is None:
        return None
    return active_ms / duration_ms


def extract_pet_owner_map(actors: list[dict[str, Any]]) -> dict[int, int]:
    """docs/schema_confirmado.md §3: `masterData.actors[].petOwner` is the
    pet -> owning-player mapping needed to attribute pet damage correctly
    (§5's confirmed pet-attribution method). actor_id -> owner player_id,
    for every actor with a non-null petOwner.
    """
    if not isinstance(actors, list):
        return {}
    return {
        int(a["id"]): int(a["petOwner"])
        for a in actors
        if isinstance(a, dict) and a.get("petOwner") is not None and "id" in a
    }


def pet_ids_for_owner(pet_owner_map: dict[int, int], player_id: int) -> frozenset[int]:
    return frozenset(actor_id for actor_id, owner in pet_owner_map.items() if owner == player_id)


def parse_resource_waste(events: list[dict[str, Any]], player_id: int) -> dict[int, float]:
    """Sums `waste` per `resourceChangeType` (docs/schema_confirmado.md
    §10) for this player's own resourcechange events — never a pet's.
    """
    waste: dict[int, float] = {}
    for ev in events:
        if ev.get("type") != "resourcechange" or ev.get("sourceID") != player_id:
            continue
        rtype = ev.get("resourceChangeType")
        if rtype is None:
            continue
        waste[int(rtype)] = waste.get(int(rtype), 0.0) + float(ev.get("waste") or 0)
    return waste


def parse_resource_waste_by_ability(
    events: list[dict[str, Any]], player_id: int
) -> dict[int, dict[int, float]]:
    """Preserve waste by resource type and ability from existing event pages."""
    waste: dict[int, dict[int, float]] = {}
    for ev in events:
        if ev.get("type") != "resourcechange" or ev.get("sourceID") != player_id:
            continue
        rtype = ev.get("resourceChangeType")
        ability_id = ev.get("abilityGameID")
        if rtype is None or ability_id is None:
            continue
        by_ability = waste.setdefault(int(rtype), {})
        ability_id = int(ability_id)
        by_ability[ability_id] = by_ability.get(ability_id, 0.0) + float(ev.get("waste") or 0)
    return waste


def parse_resource_type_counts(
    events: list[dict[str, Any]], player_id: int
) -> tuple[dict[int, tuple[int, float]], frozenset[int]]:
    """M3.1 (docs/m3-1-specification.md D-M31-08): same event domain as
    parse_resource_waste (this player's own resourcechange events), but
    keeps each resourceChangeType's own EVENT COUNT alongside its waste
    total — the proof that the type was actually observed, not inferred
    from a nonzero waste sum (a type with zero waste is still applicable).

    Unlike parse_resource_waste (M1, unchanged, `float(ev.get("waste") or
    0)`), an event whose `waste` is missing or non-numeric does NOT count
    toward the returned subtotal: stream-availability's AVAILABLE means an
    OBSERVED value, and docs/schema_confirmado.md §10 confirms `waste` is
    always present on a real resourcechange event, so a missing one has
    nothing to observe — counting it as a zero-waste occurrence would
    fabricate the very "zero observed" state D-M31-04 requires proof for.

    R2 (independent review, residual): silently dropping that event is not
    enough on its own — if this player has ANOTHER event of the SAME type
    that IS usable, the subtotal would still look like a complete measure
    of the whole fight for that type. The second return value is exactly
    the set of types with at least one unusable event, so a caller can
    degrade that type's status even though every OTHER type, and the
    stream as a whole, may still be COMPLETE.
    """
    counts: dict[int, int] = {}
    waste: dict[int, float] = {}
    incomplete_types: set[int] = set()
    for ev in events:
        if ev.get("type") != "resourcechange" or ev.get("sourceID") != player_id:
            continue
        rtype = ev.get("resourceChangeType")
        if rtype is None:
            continue
        rtype = int(rtype)
        raw_waste = ev.get("waste")
        if (
            isinstance(raw_waste, bool)
            or not isinstance(raw_waste, (int, float))
            or not math.isfinite(raw_waste)
        ):
            incomplete_types.add(rtype)
            continue
        counts[rtype] = counts.get(rtype, 0) + 1
        waste[rtype] = waste.get(rtype, 0.0) + float(raw_waste)
    return {rtype: (counts[rtype], waste[rtype]) for rtype in counts}, frozenset(incomplete_types)


def parse_aura_table_provenance(table_data: Any, duration_ms: float) -> AuraTableProvenance:
    """M3.1 (D-M31-03/D-M31-08): classifies coverage of ONE Buffs/Debuffs
    table response — COMPLETE only when it answered with a positive
    `totalTime` EXACTLY equal to this fight's own duration in ms (a
    permanent regression test replays this against every recorded aura
    table cassette; see test_stream_availability_corpus_replay.py). The
    QUERY_PLAYER_BUFFS/QUERY_PLAYER_DEBUFFS queries take no explicit
    startTime/endTime — WCL scopes the table to the whole fight via
    `fightIDs` — so the requested interval this stream ever covers is
    `[0, duration_ms)`, recorded on every branch below, including failure.

    Independent of parse_aura_uptimes (unchanged): this keeps the table's
    own raw `totalUptime`/`totalUses` per spell for provenance, never the
    derived fraction, and never mutates the shared parsing logic that
    existing consumers depend on.

    A `guid` repeated within the SAME table with disagreeing
    `(totalUptime, totalUses)` is an intra-table conflict — the same
    refusal-to-guess D-M31-06 already applies across the Buffs/Debuffs
    pair — and is dropped entirely (order-independent: decided by the set
    of distinct representations seen, never by which came first or last),
    so `observe_aura_uptime` sees it as simply not listed in this table
    rather than picking one arbitrarily. A non-finite `totalUptime` (e.g. a
    malformed NaN token) is dropped the same way `totalUptime is None`
    already was — never serialized (D-M31-08 requires canonical JSON with
    no NaN) and never fabricated as a value.
    """
    requested_start_ms = 0.0
    requested_end_ms = duration_ms
    if not isinstance(table_data, dict):
        return AuraTableProvenance(
            CollectionProvenance(
                CollectionStatus.UNKNOWN,
                ("STREAM_UNAVAILABLE",),
                requested_start_ms,
                requested_end_ms,
            )
        )
    auras = table_data.get("auras")
    total_time = table_data.get("totalTime")
    valid_shape = (
        isinstance(auras, list)
        and isinstance(total_time, (int, float))
        and not isinstance(total_time, bool)
        and total_time > 0
    )
    if not valid_shape or not isinstance(auras, list) or not isinstance(total_time, (int, float)):
        return AuraTableProvenance(
            CollectionProvenance(
                CollectionStatus.UNKNOWN,
                ("STREAM_UNAVAILABLE",),
                requested_start_ms,
                requested_end_ms,
            )
        )
    seen: dict[int, set[tuple[float, int]]] = {}
    for a in auras:
        if not isinstance(a, dict) or "guid" not in a:
            continue
        uptime_ms = a.get("totalUptime")
        if uptime_ms is None:
            continue
        if isinstance(uptime_ms, bool) or not isinstance(uptime_ms, (int, float)):
            continue
        if not math.isfinite(uptime_ms):
            continue
        entry = (float(uptime_ms), int(a.get("totalUses") or 0))
        seen.setdefault(int(a["guid"]), set()).add(entry)
    raw = {guid: next(iter(values)) for guid, values in seen.items() if len(values) == 1}
    total_time_ms = float(total_time)
    if total_time_ms != duration_ms:
        return AuraTableProvenance(
            CollectionProvenance(
                CollectionStatus.PARTIAL,
                ("AURA_TABLE_TOTAL_TIME_MISMATCH",),
                requested_start_ms,
                requested_end_ms,
            ),
            total_time_ms=total_time_ms,
            auras=raw,
        )
    return AuraTableProvenance(
        CollectionProvenance(CollectionStatus.COMPLETE, (), requested_start_ms, requested_end_ms),
        total_time_ms=total_time_ms,
        auras=raw,
    )


def classify_resource_stream_provenance(raw: CollectionProvenance) -> CollectionProvenance:
    """M3.1 (D-M31-03): translates `_paginate_events`'s own
    CollectionProvenance (ingest/performance_fetch.py) into the
    stream-availability vocabulary, keeping its original reasons intact
    (never discarding them, unlike `fetch_resource_waste`'s prior `_`).
    """
    if raw.status is CollectionStatus.COMPLETE:
        return raw
    canonical_status = (
        CollectionStatus.UNKNOWN
        if raw.status is CollectionStatus.UNKNOWN
        else CollectionStatus.PARTIAL
    )
    canonical_reason = (
        "STREAM_UNAVAILABLE" if raw.status is CollectionStatus.UNKNOWN else "STREAM_PARTIAL"
    )
    return CollectionProvenance(
        canonical_status,
        (canonical_reason, *raw.reasons),
        raw.requested_start_ms,
        raw.requested_end_ms,
    )
