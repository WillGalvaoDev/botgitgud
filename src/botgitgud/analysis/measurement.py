"""M1 measurement contracts and deterministic damage accounting."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from botgitgud.analysis.metric_population import MetricPopulationSet
    from botgitgud.analysis.performance_features import ScalarFinding

from botgitgud.domain.damage_scope import DamageScopeVersion
from botgitgud.domain.measurement_validation import collection_interval_problem
from botgitgud.domain.models import CollectionStatus, PlayerLog


class MetricStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class MetricObservation:
    metric_id: str
    value: float | None
    unit: str
    status: MetricStatus
    reasons: tuple[str, ...] = ()
    denominator_kind: str = ""
    source_identity: str = ""

    def __post_init__(self) -> None:
        if self.status is MetricStatus.AVAILABLE:
            if self.value is None or not math.isfinite(self.value) or self.reasons:
                raise ValueError("AVAILABLE metric requires finite value and no reasons")
        elif self.status in (
            MetricStatus.UNKNOWN,
            MetricStatus.NOT_APPLICABLE,
            MetricStatus.INVALID,
        ) and (self.value is not None or not self.reasons):
            raise ValueError("non-available metric requires None and a reason")
        elif self.status is MetricStatus.PARTIAL:
            if self.value is not None and (not math.isfinite(self.value) or self.value < 0):
                raise ValueError("PARTIAL metric subtotal must be finite and non-negative")
            if not self.reasons:
                raise ValueError("PARTIAL metric requires a reason")


@dataclass(frozen=True, slots=True)
class DamageAccounting:
    measurement_version: str
    status: MetricStatus
    reasons: tuple[str, ...]
    damage_scope: str
    duration_s: float
    gross_damage_by_ability: Mapping[int, float]
    gross_damage_total: float
    support_subtracted_damage: float
    net_damage: float | None
    gross_dps: float | None
    support_dps: float | None
    net_dps: float | None
    wcl_reported_dps: float | None = None
    damage_table_total: float | None = None
    reconciliation_residual: float | None = None


@dataclass(frozen=True, slots=True)
class DamageComparison:
    measurement_version: str
    method: str
    player: DamageAccounting
    reference_ids: tuple[str, ...]
    excluded_references: Mapping[str, str]
    reference_n: int
    reference_mean_net_dps: float | None
    total_delta_dps: float | None
    ability_delta_dps: Mapping[int, float]
    support_delta_dps: float | None
    residual_dps: float | None
    gap_vs_reference_pct: float | None = None
    total_delta_player_pp: float | None = None
    support_delta_player_pp: float | None = None
    status: MetricStatus = MetricStatus.UNKNOWN
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MetricComparison:
    metric_id: str
    player: MetricObservation
    reference_ids: tuple[str, ...]
    reference_values: tuple[float, ...]
    excluded_references: Mapping[str, str]
    finding: ScalarFinding | None = None
    reasons: tuple[str, ...] = ()
    # M2.3 §5.3: set only in compare_metrics' population mode. Carries the
    # full M2.2 MetricPopulationSet (descriptive + aspirational) this
    # comparison's reference_ids/reference_values/excluded_references were
    # taken from verbatim, for provenance and for consumers that need more
    # than the single dominant exclusion code excluded_references keeps.
    population: MetricPopulationSet | None = None

    def __post_init__(self) -> None:
        if self.player.metric_id != self.metric_id:
            raise ValueError("metric comparison identity mismatch")


def damage_reference_id(log: PlayerLog) -> str:
    return f"{log.fight.report_code}:{log.fight.fight_id}:{log.build.character_name}"


def measured_median(values: Sequence[float]) -> float | None:
    """Median of finite, nonnegative measured DPS without overflowing a midpoint."""
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    low, high = ordered[middle - 1], ordered[middle]
    return low + (high - low) / 2


def account_damage(log: PlayerLog) -> DamageAccounting:
    duration = float(log.fight.duration_s)
    raw_values = {int(k): float(v.total) for k, v in log.damage_by_ability.items()}
    values = {k: v for k, v in raw_values.items() if v > 0}
    try:
        gross = math.fsum(values[k] for k in sorted(values))
    except OverflowError:
        gross = math.inf
    support = float(log.support_subtracted_damage)
    numeric_valid = (
        duration > 0
        and math.isfinite(duration)
        and all(math.isfinite(v) and v >= 0 for v in raw_values.values())
        and math.isfinite(gross)
        and math.isfinite(support)
        and support >= 0
        and support <= gross
    )
    provenance = log.measurement_provenance
    if not numeric_valid:
        status, reasons = MetricStatus.INVALID, ("INVALID_DAMAGE_INPUT",)
    elif log.damage_scope is DamageScopeVersion.UNRECONCILED:
        status, reasons = MetricStatus.UNKNOWN, ("UNRECONCILED_DAMAGE_SCOPE",)
    elif log.damage_scope is DamageScopeVersion.LEGACY_UNSCOPED:
        status, reasons = MetricStatus.PARTIAL, ("LEGACY_UNSCOPED_SUBTOTAL",)
    elif provenance is None:
        status, reasons = MetricStatus.AVAILABLE, ("LEGACY_RECONCILED_TOTAL",)
    elif provenance.damage_collection.status is CollectionStatus.PARTIAL:
        status, reasons = MetricStatus.PARTIAL, ("PARTIAL_DAMAGE_COLLECTION",)
    elif provenance.damage_collection.status is CollectionStatus.UNKNOWN:
        status, reasons = MetricStatus.UNKNOWN, ("UNKNOWN_DAMAGE_COLLECTION",)
    elif problem := collection_interval_problem(provenance.damage_collection, duration):
        status, reasons = MetricStatus.INVALID, (problem,)
    elif provenance.damage_reconciliation_status != log.damage_scope.value or (
        provenance.damage_reconciliation_residual is not None
        and provenance.damage_reconciliation_residual != 0
    ):
        status, reasons = MetricStatus.INVALID, ("CONTRADICTORY_RECONCILIATION_METADATA",)
    elif provenance.damage_table_total is None:
        status, reasons = MetricStatus.UNKNOWN, ("DAMAGE_TABLE_AUTHORITY_MISSING",)
    else:
        status, reasons = MetricStatus.AVAILABLE, ()
    can_normalize = numeric_valid and status is MetricStatus.AVAILABLE
    net = gross - support if numeric_valid else None
    residual = None
    authority = (
        log.measurement_provenance.damage_table_total if log.measurement_provenance else None
    )
    if authority is not None and status is MetricStatus.AVAILABLE:
        assert net is not None
        residual = net - float(authority)
        if residual != 0:
            status = MetricStatus.INVALID
            reasons = ("DAMAGE_TABLE_RECONCILIATION_MISMATCH",)
            can_normalize = False
    gross_dps = gross / duration if can_normalize else None
    support_dps = support / duration if can_normalize else None
    net_dps = net / duration if can_normalize and net is not None else None
    if can_normalize and any(
        v is None or not math.isfinite(v) for v in (gross_dps, support_dps, net_dps)
    ):
        status, reasons = MetricStatus.INVALID, ("NONFINITE_DERIVED_DAMAGE",)
        gross_dps = support_dps = net_dps = None
    if status is not MetricStatus.AVAILABLE:
        net = None
    return DamageAccounting(
        measurement_version="damage-accounting-v2",
        status=status,
        reasons=reasons,
        damage_scope=log.damage_scope.value,
        duration_s=duration,
        gross_damage_by_ability=values,
        gross_damage_total=gross,
        support_subtracted_damage=support,
        net_damage=net,
        gross_dps=gross_dps,
        support_dps=support_dps,
        net_dps=net_dps,
        wcl_reported_dps=log.dps,
        damage_table_total=authority,
        reconciliation_residual=residual,
    )


def compare_damage(player: PlayerLog, references: tuple[PlayerLog, ...]) -> DamageComparison:
    p = account_damage(player)
    excluded: dict[str, str] = {}
    eligible_list: list[tuple[str, DamageAccounting]] = []
    identities: dict[str, PlayerLog] = {}
    conflicts: set[str] = set()
    for reference in references:
        identity = damage_reference_id(reference)
        if identity in identities and identities[identity] != reference:
            conflicts.add(identity)
        identities[identity] = reference
    for reference in references:
        ref_id = damage_reference_id(reference)
        if ref_id in conflicts:
            excluded[ref_id] = "CONFLICTING_REFERENCE_IDENTITY"
            continue
        if reference.damage_scope != player.damage_scope:
            excluded[ref_id] = "SCOPE_MISMATCH"
            continue
        if reference.damage_scope.value in {"legacy_unscoped", "unreconciled"}:
            excluded[ref_id] = "NON_QUANTITATIVE_SCOPE"
            continue
        accounting = account_damage(reference)
        if accounting.status is not MetricStatus.AVAILABLE:
            excluded[ref_id] = accounting.reasons[0] if accounting.reasons else "INVALID_ACCOUNTING"
            continue
        eligible_list.append((ref_id, accounting))
    eligible = tuple(sorted(eligible_list, key=lambda item: item[0]))
    usable = tuple((i, a) for i, a in eligible if a.status is MetricStatus.AVAILABLE)
    mean = (
        math.fsum(a.net_dps / len(usable) for _, a in usable if a.net_dps is not None)
        if usable
        else None
    )
    ability_ids = set(p.gross_damage_by_ability)
    for _, a in usable:
        ability_ids.update(a.gross_damage_by_ability)
    deltas = (
        {
            sid: (
                math.fsum(
                    (
                        p.gross_damage_by_ability.get(sid, 0.0) / p.duration_s
                        - a.gross_damage_by_ability.get(sid, 0.0) / a.duration_s
                    )
                    / len(usable)
                    for _, a in usable
                )
            )
            for sid in sorted(ability_ids)
        }
        if usable and p.status is MetricStatus.AVAILABLE
        else {}
    )
    player_available = p.status is MetricStatus.AVAILABLE
    total = (
        p.net_dps - mean
        if player_available and p.net_dps is not None and mean is not None
        else None
    )
    support = (
        (
            math.fsum(
                (a.support_dps - p.support_dps) / len(usable)
                for _, a in usable
                if a.support_dps is not None
            )
        )
        if player_available and usable and p.support_dps is not None
        else None
    )
    try:
        residual = (
            math.fsum((*deltas.values(), support, -total))
            if total is not None and support is not None
            else None
        )
    except (OverflowError, ValueError):
        residual = math.inf
    status = (
        p.status
        if not player_available
        else MetricStatus.AVAILABLE
        if usable
        else MetricStatus.UNKNOWN
    )
    reasons = p.reasons if not player_available else () if usable else ("NO_REFERENCES",)
    if residual is not None:
        assert support is not None and total is not None
        tolerance = max(
            1e-9, math.fsum(abs(value) * 1e-12 for value in (*deltas.values(), support, total))
        )
        if not math.isfinite(residual) or abs(residual) > tolerance:
            status, reasons = MetricStatus.INVALID, ("DAMAGE_COMPARISON_CLOSURE_ERROR",)
            mean = total = residual = None
            deltas = {}
            support = None
    gap_pct = 100 * (total / mean) if total is not None and mean and mean > 0 else None
    total_pp = (
        100 * (total / p.net_dps) if total is not None and p.net_dps and p.net_dps > 0 else None
    )
    support_pp = (
        100 * (support / p.net_dps) if support is not None and p.net_dps and p.net_dps > 0 else None
    )
    derived = (mean, total, support, residual, gap_pct, total_pp, support_pp, *deltas.values())
    if any(value is not None and not math.isfinite(value) for value in derived):
        status, reasons = MetricStatus.INVALID, ("NONFINITE_DERIVED_COMPARISON",)
        mean = total = support = residual = gap_pct = total_pp = support_pp = None
        deltas = {}
    return DamageComparison(
        "damage-comparison-v2",
        "paired_mean",
        p,
        tuple(i for i, _ in usable),
        dict(sorted(excluded.items())),
        len(usable),
        mean,
        total,
        deltas,
        support,
        residual,
        gap_pct,
        total_pp,
        support_pp,
        status,
        reasons,
    )
