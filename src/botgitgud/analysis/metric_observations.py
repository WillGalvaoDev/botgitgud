"""M1 per-metric observations: absence, eligibility and source identity."""

import math
from collections.abc import Sequence

from botgitgud.analysis.measurement import (
    MetricComparison,
    MetricObservation,
    MetricStatus,
    account_damage,
    damage_reference_id,
)
from botgitgud.analysis.performance_features import grade_scalar, scalar_is_finite
from botgitgud.domain.measurement_validation import collection_interval_problem, valid_player_casts
from botgitgud.domain.models import CollectionStatus, PlayerLog
from botgitgud.domain.spells import SpellCatalog

UNITS = {
    "gross_ability_dps": ("DPS", "FIGHT_DURATION_SECONDS"),
    "player_casts_per_minute": ("casts/min", "FIGHT_DURATION_MINUTES"),
    "damage_events_per_second": ("events/s", "FIGHT_DURATION_SECONDS"),
    "damage_per_event": ("damage/event", "DAMAGE_EVENTS"),
    "aura_uptime_fraction": ("fraction", "FIGHT_DURATION_SECONDS"),
    "gross_damage_share_pct": ("percent", "GROSS_DAMAGE"),
}


def observe(
    log: PlayerLog, sid: int, metric: str, catalog: SpellCatalog | None
) -> MetricObservation:
    unit, denominator = UNITS[metric]
    identity = damage_reference_id(log)

    def result(
        value: float | None,
        status: MetricStatus = MetricStatus.AVAILABLE,
        reason: str | None = None,
    ) -> MetricObservation:
        if status is MetricStatus.AVAILABLE and (value is None or not math.isfinite(value)):
            value, status, reason = None, MetricStatus.INVALID, "NONFINITE_DERIVED_METRIC"
        return MetricObservation(
            f"{metric}:{sid}",
            value,
            unit,
            status,
            (reason,) if reason else (),
            denominator,
            identity,
        )

    if catalog is not None and catalog.identity(sid).resolution_status == "unresolved":
        return result(None, MetricStatus.UNKNOWN, "UNRESOLVED_IDENTITY")
    duration = log.fight.duration_s
    if not math.isfinite(duration) or duration <= 0:
        return result(None, MetricStatus.INVALID, "INVALID_DURATION")
    if metric == "aura_uptime_fraction":
        if sid not in log.uptimes:
            return result(None, MetricStatus.UNKNOWN, "UPTIME_NOT_OBSERVED")
        value = log.uptimes[sid]
        if not math.isfinite(value) or not 0 <= value <= 1:
            return result(None, MetricStatus.INVALID, "INVALID_UPTIME")
        return result(value)
    provenance = log.measurement_provenance
    if metric == "player_casts_per_minute":
        if provenance is None or provenance.casts_collection.status is CollectionStatus.UNKNOWN:
            return result(None, MetricStatus.UNKNOWN, "CAST_COVERAGE_UNKNOWN")
        if provenance.casts_collection.status is CollectionStatus.PARTIAL:
            return result(None, MetricStatus.PARTIAL, "PARTIAL_CAST_COLLECTION")
        if problem := collection_interval_problem(provenance.casts_collection, duration):
            return result(None, MetricStatus.INVALID, problem)
        times = log.cast_timeline.get(sid, ())
        if not times:
            return result(None, MetricStatus.NOT_APPLICABLE, "CAST_MECHANISM_NOT_OBSERVED")
        if not valid_player_casts(log, sid):
            return result(None, MetricStatus.INVALID, "INVALID_CAST_TIMESTAMP")
        return result(60 * len(times) / duration)
    accounting = account_damage(log)
    if accounting.status is not MetricStatus.AVAILABLE:
        return result(None, accounting.status, accounting.reasons[0])
    ability = log.damage_by_ability.get(sid)
    if ability is None or (
        ability.total <= 0 and metric in {"gross_ability_dps", "gross_damage_share_pct"}
    ):
        return result(None, MetricStatus.NOT_APPLICABLE, "PLAYER_MECHANISM_NOT_OBSERVED")
    if metric == "gross_ability_dps":
        return result(ability.total / duration)
    if metric == "gross_damage_share_pct":
        return result(100 * (ability.total / accounting.gross_damage_total))
    if provenance is None or provenance.damage_collection.status is not CollectionStatus.COMPLETE:
        return result(None, MetricStatus.UNKNOWN, "EVENT_COVERAGE_UNKNOWN")
    hits = ability.hits
    if not math.isfinite(hits) or hits < 0 or hits != int(hits):
        return result(None, MetricStatus.INVALID, "INVALID_EVENT_COUNT")
    if metric == "damage_events_per_second":
        return result(hits / duration)
    if hits == 0:
        return result(None, MetricStatus.UNKNOWN, "DAMAGE_EVENT_DENOMINATOR_UNAVAILABLE")
    return result(hits / duration if metric == "damage_events_per_second" else ability.total / hits)


def compare_metrics(
    player: PlayerLog,
    references: Sequence[PlayerLog],
    catalog: SpellCatalog,
    *,
    metric_names: Sequence[str] = tuple(UNITS),
) -> dict[str, MetricComparison]:
    """Each available scalar uses only its own observations; no cross-metric N."""
    ids = set(player.damage_by_ability) | set(player.cast_timeline) | set(player.uptimes)
    by_identity: dict[str, PlayerLog] = {}
    conflicts = set()
    for ref in references:
        identity = damage_reference_id(ref)
        if identity in by_identity and by_identity[identity] != ref:
            conflicts.add(identity)
        by_identity[identity] = ref
        ids.update(ref.damage_by_ability)
        ids.update(ref.cast_timeline)
        ids.update(ref.uptimes)
    comparisons = {}
    for sid in sorted(ids):
        for metric in metric_names:
            observation = observe(player, sid, metric, catalog)
            used, values, excluded = [], [], {}
            for ref in sorted(references, key=damage_reference_id):
                identity = damage_reference_id(ref)
                if identity in conflicts:
                    excluded[identity] = "CONFLICTING_REFERENCE_IDENTITY"
                    continue
                if (
                    metric not in {"player_casts_per_minute", "aura_uptime_fraction"}
                    and ref.damage_scope != player.damage_scope
                ):
                    excluded[identity] = "SCOPE_MISMATCH"
                    continue
                value = observe(ref, sid, metric, catalog)
                if value.status is MetricStatus.AVAILABLE:
                    used.append(identity)
                    assert value.value is not None
                    values.append(value.value)
                else:
                    excluded[identity] = value.reasons[0]
            scalar = (
                grade_scalar(observation.value, values, "higher_better")
                if observation.status is MetricStatus.AVAILABLE
                and observation.value is not None
                and values
                and metric
                in {"gross_ability_dps", "player_casts_per_minute", "aura_uptime_fraction"}
                else None
            )
            reasons = ()
            if scalar is not None and not scalar_is_finite(scalar):
                scalar, reasons = None, ("NONFINITE_DERIVED_STATISTIC",)
            comparisons[observation.metric_id] = MetricComparison(
                observation.metric_id,
                observation,
                tuple(used),
                tuple(values),
                excluded,
                scalar,
                reasons,
            )
    return comparisons
