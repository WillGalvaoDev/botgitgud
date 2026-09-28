"""M3.1 — stream-availability-v1 (docs/m3-1-specification.md): coverage,
aplicabilidade e proveniência das auras (Buffs/Debuffs) e do desperdício de
recursos, na interface de stream. Local: nenhum consumidor existente é
religado a este módulo (M3.4) — `metric_observations.observe`,
`performance_features`, `findings`, `remediation`, `report` e `phase4`
continuam lendo os campos legados (`uptimes`, `aura_details`,
`resource_waste`, `resource_waste_by_ability`, `external_buffs`,
`has_augmentation`), inalterados.

Funções puras e deterministas: dependem só do ``PlayerLog`` recebido, nunca
de I/O, ordem de chamada ou estado externo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from botgitgud.analysis.measurement import MetricObservation, MetricStatus, damage_reference_id
from botgitgud.domain.models import CollectionProvenance, CollectionStatus, PlayerLog
from botgitgud.domain.resource_types import resource_type_from_label

STREAM_AVAILABILITY_POLICY_VERSION = "stream-availability-v1"

# Ordering used only to pick the WORSE of two non-COMPLETE table states
# (D-M31-04's "herda o pior estado") — never used to compare with COMPLETE
# itself, which always short-circuits its own branch first.
_SEVERITY = {
    CollectionStatus.COMPLETE: 0,
    CollectionStatus.PARTIAL: 1,
    CollectionStatus.UNKNOWN: 2,
}


class Stream(StrEnum):
    """D-M31-01: the finite list of streams M3.1 covers. Dano and casts
    stay on M1's own CollectionProvenance (measurement_provenance);
    they are not part of this enum.
    """

    AURA_BUFFS = "AURA_BUFFS"
    AURA_DEBUFFS = "AURA_DEBUFFS"
    RESOURCES = "RESOURCES"


@dataclass(frozen=True, slots=True)
class ExternalBuffsAvailability:
    """F8: `has_augmentation`/`external_buffs` derive only from the Buffs
    table — this reports THAT table's own coverage, so a future consumer
    (M3.4) can tell a reliable "no external buffs" from a table that never
    answered, instead of treating both the same way.
    """

    status: MetricStatus
    reasons: tuple[str, ...] = ()


def stream_state(log: PlayerLog, stream: Stream) -> CollectionProvenance:
    """D-M31-07: a log fetched before M3.1 carries no ``stream_provenance``
    at all — declares STREAM_COVERAGE_UNRECORDED rather than guess
    completeness or absence from its flat legacy fields.
    """
    provenance = log.stream_provenance
    if provenance is None:
        return CollectionProvenance(CollectionStatus.UNKNOWN, ("STREAM_COVERAGE_UNRECORDED",))
    table = {
        Stream.AURA_BUFFS: provenance.buffs,
        Stream.AURA_DEBUFFS: provenance.debuffs,
        Stream.RESOURCES: provenance.resources,
    }[stream]
    if table is None:
        return CollectionProvenance(CollectionStatus.UNKNOWN, ("STREAM_COVERAGE_UNRECORDED",))
    return table.collection


def _worse(a: CollectionProvenance, b: CollectionProvenance) -> CollectionProvenance:
    """Deterministic, order-independent combination of two non-COMPLETE
    states (D-M31-04): higher severity (UNKNOWN > PARTIAL) wins; a tie
    merges both reasons (sorted, deduplicated) so neither failure is
    silently dropped, regardless of which table is passed first.
    """
    if _SEVERITY[a.status] > _SEVERITY[b.status]:
        return a
    if _SEVERITY[b.status] > _SEVERITY[a.status]:
        return b
    return CollectionProvenance(a.status, tuple(sorted(set(a.reasons) | set(b.reasons))))


def observe_aura_uptime(log: PlayerLog, spell_id: int) -> MetricObservation:
    """D-M31-04/D-M31-06/D-M31-07: reads BOTH aura tables (never just one),
    never treats an aura absent from a COMPLETE table as zero, and refuses
    to guess between two COMPLETE tables that disagree.
    """
    metric_id = f"aura_uptime_fraction:{spell_id}"
    identity = damage_reference_id(log)

    def result(
        value: float | None,
        status: MetricStatus = MetricStatus.AVAILABLE,
        reasons: tuple[str, ...] = (),
    ) -> MetricObservation:
        return MetricObservation(
            metric_id, value, "fraction", status, reasons, "FIGHT_DURATION_SECONDS", identity
        )

    duration = log.fight.duration_s
    if not math.isfinite(duration) or duration <= 0:
        return result(None, MetricStatus.INVALID, ("INVALID_DURATION",))

    provenance = log.stream_provenance
    if provenance is None:
        # D-M31-07: a single agreeing legacy response is accepted as-is —
        # the same precedent M1 already gives an unversioned, COMPLETE-only
        # response for other measures.
        if spell_id not in log.uptimes:
            return result(None, MetricStatus.UNKNOWN, ("STREAM_COVERAGE_UNRECORDED",))
        value = log.uptimes[spell_id]
        if not math.isfinite(value) or not 0 <= value <= 1:
            return result(None, MetricStatus.INVALID, ("INVALID_UPTIME",))
        return result(value)

    buffs_state = stream_state(log, Stream.AURA_BUFFS)
    debuffs_state = stream_state(log, Stream.AURA_DEBUFFS)

    listings: list[float] = []
    if buffs_state.status is CollectionStatus.COMPLETE and provenance.buffs is not None:
        entry = provenance.buffs.auras.get(spell_id)
        if entry is not None:
            uptime_ms, _total_uses = entry
            listings.append(uptime_ms / provenance.buffs.total_time_ms)  # type: ignore[operator]
    if debuffs_state.status is CollectionStatus.COMPLETE and provenance.debuffs is not None:
        entry = provenance.debuffs.auras.get(spell_id)
        if entry is not None:
            uptime_ms, _total_uses = entry
            listings.append(uptime_ms / provenance.debuffs.total_time_ms)  # type: ignore[operator]

    if listings:
        if len(listings) > 1 and listings[0] != listings[1]:
            return result(None, MetricStatus.INVALID, ("AURA_TABLE_CONFLICT",))
        value = listings[0]
        if not math.isfinite(value) or not 0 <= value <= 1:
            return result(None, MetricStatus.INVALID, ("INVALID_UPTIME",))
        return result(value)

    if (
        buffs_state.status is CollectionStatus.COMPLETE
        and debuffs_state.status is CollectionStatus.COMPLETE
    ):
        return result(None, MetricStatus.UNKNOWN, ("AURA_NOT_LISTED",))

    worse = _worse(buffs_state, debuffs_state)
    status = (
        MetricStatus.PARTIAL if worse.status is CollectionStatus.PARTIAL else MetricStatus.UNKNOWN
    )
    return result(None, status, worse.reasons)


def observe_resource_waste(log: PlayerLog, resource_type: int) -> MetricObservation:
    """D-M31-04/D-M31-05/D-M31-07: a resource type observed with zero
    waste in a COMPLETE collection is zero observed, never confused with a
    type that was never seen (RESOURCE_TYPE_NOT_OBSERVED) or a partial
    collection's subtotal.

    R2 (independent review, residual): a COMPLETE stream only proves the
    PAGINATION finished; it says nothing about whether every individual
    event of THIS type had a usable `waste`. `resource_type in
    incomplete_types` means at least one of this player's own events of
    this type could not be measured — the subtotal `by_type` holds (if
    any) sums only the usable ones, so it is presented as PARTIAL, exactly
    like a stream-level partial subtotal, never as the fight's proven
    total for that type.
    """
    metric_id = f"resource_waste_per_minute:{resource_type}"
    identity = damage_reference_id(log)
    unit = f"{resource_type} units/min"

    def result(
        value: float | None,
        status: MetricStatus = MetricStatus.AVAILABLE,
        reasons: tuple[str, ...] = (),
    ) -> MetricObservation:
        return MetricObservation(
            metric_id, value, unit, status, reasons, "FIGHT_DURATION_MINUTES", identity
        )

    duration = log.fight.duration_s
    if not math.isfinite(duration) or duration <= 0:
        return result(None, MetricStatus.INVALID, ("INVALID_DURATION",))

    provenance = log.stream_provenance
    if provenance is None:
        # D-M31-07: unlike aura uptime, no legacy resource-waste value is
        # ever accepted — the historical paginator recorded no coverage
        # proof at all for this stream.
        return result(None, MetricStatus.UNKNOWN, ("STREAM_COVERAGE_UNRECORDED",))

    resource_state = stream_state(log, Stream.RESOURCES)
    by_type = provenance.resources.by_type if provenance.resources is not None else {}
    incomplete_types = (
        provenance.resources.incomplete_types if provenance.resources is not None else frozenset()
    )
    entry = by_type.get(resource_type)

    if resource_state.status is CollectionStatus.UNKNOWN:
        return result(None, MetricStatus.UNKNOWN, resource_state.reasons)

    if resource_state.status is CollectionStatus.PARTIAL:
        value = (60.0 * entry[1] / duration) if entry is not None else None
        return result(value, MetricStatus.PARTIAL, resource_state.reasons)

    # COMPLETE pagination, but this type had at least one unusable event:
    # never present its (possibly partial, possibly absent) subtotal as an
    # integral measure.
    if resource_type in incomplete_types:
        value = (60.0 * entry[1] / duration) if entry is not None else None
        return result(value, MetricStatus.PARTIAL, ("STREAM_PARTIAL",))

    if entry is None:
        return result(None, MetricStatus.UNKNOWN, ("RESOURCE_TYPE_NOT_OBSERVED",))
    _count, waste_total = entry
    return result(60.0 * waste_total / duration)


def external_buffs_state(log: PlayerLog) -> ExternalBuffsAvailability:
    """F8: `has_augmentation`/`external_buffs` (domain/models.py's
    PlayerBuild) derive only from the AURA_BUFFS table — this reports that
    table's own coverage, never re-derives the boolean/set themselves.
    """
    state = stream_state(log, Stream.AURA_BUFFS)
    if state.status is CollectionStatus.COMPLETE:
        return ExternalBuffsAvailability(MetricStatus.AVAILABLE)
    status = (
        MetricStatus.PARTIAL if state.status is CollectionStatus.PARTIAL else MetricStatus.UNKNOWN
    )
    return ExternalBuffsAvailability(status, state.reasons)


def observe_legacy_resource_label(log: PlayerLog, label: str) -> MetricObservation:
    """D-M31-05/AC4: the reachable caller `resource_type_from_label`'s own
    docstring promised — classifies a label from the LEGACY, string-keyed
    `PlayerLog.resource_waste` (M1) by IDENTITY first, independent of
    coverage: a label that cannot be traced back to any
    `resourceChangeType` at all is INVALID/LEGACY_RESOURCE_LABEL_UNMAPPABLE
    regardless of what `stream_provenance` says, since there is nothing to
    look up coverage FOR.

    A resolvable label defers entirely to `observe_resource_waste`'s own
    decision for that integer type — this never fabricates coverage and
    never changes D-M31-07's gate: a resolvable label on a historical log
    (no `stream_provenance`) still comes back
    UNKNOWN/STREAM_COVERAGE_UNRECORDED, exactly as before this function
    existed.
    """
    resource_type = resource_type_from_label(label)
    if resource_type is None:
        return MetricObservation(
            f"resource_waste_per_minute:{label}",
            None,
            f"{label} units/min",
            MetricStatus.INVALID,
            ("LEGACY_RESOURCE_LABEL_UNMAPPABLE",),
            "FIGHT_DURATION_MINUTES",
            damage_reference_id(log),
        )
    return observe_resource_waste(log, resource_type)
