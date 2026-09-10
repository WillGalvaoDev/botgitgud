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

from botgitgud.domain.models import AbilityDamage, AbilitySourceDamage


@dataclass(frozen=True, slots=True)
class RawDamageEvent:
    spell_id: int
    source_id: int
    target_id: int
    amount: float


def parse_damage_events(
    events: Sequence[dict],
    source_ids: frozenset[int],
    target_ids: frozenset[int] | None = None,
) -> list[RawDamageEvent]:
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
        if target_ids is not None and target_id not in target_ids:
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


def support_subtracted_total(
    events: Sequence[dict],
    player_id: int,
    pet_owner_by_actor: Mapping[int, int],
    target_ids: frozenset[int] | None = None,
) -> float:
    """Return the raw WCL support-attribution term owned by ``player_id``."""
    total = 0.0
    for event in events:
        if event.get("type") != "damage" or not event.get("subtractsFromSupportedActor"):
            continue
        support_id = event.get("supportID")
        if support_id is None:
            continue
        if target_ids is not None and event.get("targetID") not in target_ids:
            continue
        owner_id = pet_owner_by_actor.get(int(support_id), int(support_id))
        if owner_id == player_id:
            total += float((event.get("amount") or 0) + (event.get("absorbed") or 0))
    return total


def scope_total(damage_by_ability: Mapping[int, AbilityDamage], support_subtracted: float) -> float:
    """Reconciled total without inventing an ability for support attribution."""
    return sum(ability.total for ability in damage_by_ability.values()) - support_subtracted


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
    by_source: dict[int, dict[int, AbilitySourceDamage]] = {}
    for ev in raw_events:
        totals[ev.spell_id] = totals.get(ev.spell_id, 0.0) + ev.amount
        hits[ev.spell_id] = hits.get(ev.spell_id, 0) + 1
        targets.setdefault(ev.spell_id, set()).add(ev.target_id)
        current = by_source.setdefault(ev.spell_id, {}).get(ev.source_id)
        by_source[ev.spell_id][ev.source_id] = AbilitySourceDamage(
            source_id=ev.source_id,
            total=(current.total if current else 0.0) + ev.amount,
            hits=(current.hits if current else 0) + 1,
        )

    damage_by_ability = {
        spell_id: AbilityDamage(
            spell_id=spell_id,
            total=totals[spell_id],
            hits=hits[spell_id],
            casts=cast_counts.get(spell_id, 0),
            by_source=by_source[spell_id],
        )
        for spell_id in totals
    }
    avg_targets_per_cast = {
        spell_id: len(targets[spell_id]) / cast_counts[spell_id]
        for spell_id in totals
        if cast_counts.get(spell_id, 0) > 0
    }
    return damage_by_ability, avg_targets_per_cast
