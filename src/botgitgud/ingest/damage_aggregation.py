"""T3.1 (docs/schema_confirmado.md §5) — player+pet damage aggregation
from raw DamageDone events. The only correct way to build a per-ability
breakdown: `entry.abilities` on the `table(dataType: DamageDone)` response
is truncated (5 of 29 real abilities for the Zarad fixture) and
`entry.pets` double-counts against `entry.total` if added to it — see
docs/desvios.md. Aggregating raw events by `abilityGameID`, restricted to
sourceID in {player_id} union {pet_ids}, reproduces `entry.total` exactly
(live-verified: 0.00% error).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from botgitgud.domain.models import AbilityDamage


@dataclass(frozen=True, slots=True)
class RawDamageEvent:
    spell_id: int
    source_id: int
    target_id: int
    amount: float


def parse_damage_events(events: Sequence[dict], source_ids: frozenset[int]) -> list[RawDamageEvent]:
    """One page's worth of `dataType: DamageDone` events, restricted to
    `source_ids` (player + their pets) — `amount + absorbed` is the true
    total per event (docs/schema_confirmado.md §5.4, verified exact).
    """
    parsed: list[RawDamageEvent] = []
    for ev in events:
        if ev.get("type") != "damage" or ev.get("sourceID") not in source_ids:
            continue
        spell_id = ev.get("abilityGameID") or ev.get("ability")
        target_id = ev.get("targetID")
        if not spell_id or target_id is None:
            continue
        amount = (ev.get("amount") or 0) + (ev.get("absorbed") or 0)
        parsed.append(
            RawDamageEvent(
                spell_id=int(spell_id),
                source_id=int(ev["sourceID"]),
                target_id=int(target_id),
                amount=float(amount),
            )
        )
    return parsed


def aggregate_damage_by_ability(
    raw_events: Sequence[RawDamageEvent], cast_counts: Mapping[int, int]
) -> tuple[dict[int, AbilityDamage], dict[int, float]]:
    """Returns (damage_by_ability, avg_targets_per_cast), both keyed by
    spell_id. `cast_counts` is the player's own cast_timeline lengths
    (0/absent for a pure pet-cast ability, e.g. a Wild Imp's Fel Firebolt —
    the player never personally cast it).

    docs/desvios.md D-29: avg_targets_per_cast is a whole-fight average
    (distinct targets hit / total casts of that ability), not a strict
    per-cast-instance average — WCL exposes no per-cast target grouping,
    and implementacao.md gives no exact formula for this feature (unlike
    T3.2's precise ones), so the obvious, cheap aggregate is used. Omitted
    (not 0.0) for a spell_id with zero of the player's own casts, since
    "average per cast" is undefined there, not zero.
    """
    totals: dict[int, float] = {}
    hits: dict[int, int] = {}
    targets: dict[int, set[int]] = {}
    for ev in raw_events:
        totals[ev.spell_id] = totals.get(ev.spell_id, 0.0) + ev.amount
        hits[ev.spell_id] = hits.get(ev.spell_id, 0) + 1
        targets.setdefault(ev.spell_id, set()).add(ev.target_id)

    damage_by_ability = {
        spell_id: AbilityDamage(
            spell_id=spell_id,
            total=totals[spell_id],
            hits=hits[spell_id],
            casts=cast_counts.get(spell_id, 0),
        )
        for spell_id in totals
    }
    avg_targets_per_cast = {
        spell_id: len(targets[spell_id]) / cast_counts[spell_id]
        for spell_id in totals
        if cast_counts.get(spell_id, 0) > 0
    }
    return damage_by_ability, avg_targets_per_cast
