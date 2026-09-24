"""M1 per-metric observations: absence, eligibility and source identity."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    # metric_population.py imports UNITS/observe from this module, so a
    # runtime import back here would cycle — TYPE_CHECKING keeps this to
    # static analysis only. compare_metrics' population mode below never
    # needs the enum values at runtime, only string equality (StrEnum).
    from botgitgud.analysis.metric_population import MetricPopulationSet

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
    populations: Mapping[str, MetricPopulationSet] | None = None,
) -> dict[str, MetricComparison]:
    """Each available scalar uses only its own observations; no cross-metric N.

    Population mode (M2.3 §5, ``populations`` given): the reference set for
    each ``metric_id`` is exactly
    ``populations[metric_id].descriptive.members`` — membership, exclusions
    and grading sufficiency come from M2.2's own ladder (``metric_population.py``,
    unmodified) and are never recomputed or filtered further here.
    ``references`` still supplies the ``PlayerLog`` objects those member ids
    resolve to (pass the same pool ``populations`` was computed over, e.g.
    the hygienic candidate set — a superset of any single metric's members);
    ``metric_names`` is ignored. The output key set is exactly
    ``populations``'s. Legacy mode (``populations=None``, the default) is
    the original behaviour, unchanged.
    """
    if populations is not None:
        return _compare_metrics_by_population(player, references, catalog, populations)

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


_GRADABLE_METRICS = frozenset(
    {"gross_ability_dps", "player_casts_per_minute", "aura_uptime_fraction"}
)


def _compare_metrics_by_population(
    player: PlayerLog,
    references: Sequence[PlayerLog],
    catalog: SpellCatalog,
    populations: Mapping[str, MetricPopulationSet],
) -> dict[str, MetricComparison]:
    id_to_log = {damage_reference_id(ref): ref for ref in references}
    comparisons: dict[str, MetricComparison] = {}
    for metric_id, population_set in populations.items():
        metric, sid_str = metric_id.split(":", 1)
        sid = int(sid_str)
        observation = observe(player, sid, metric, catalog)
        descriptive = population_set.descriptive
        used = descriptive.members
        values: list[float] = []
        for rid in used:
            value = observe(id_to_log[rid], sid, metric, catalog)
            # M2.2's own ladder already proved this id AVAILABLE for this
            # metric (that is what admits it to `descriptive.members`);
            # `observe` is pure, so re-observing the same log/metric/spell
            # must reproduce the same AVAILABLE value.
            assert value.status is MetricStatus.AVAILABLE and value.value is not None
            values.append(value.value)
        # M2.2 §10.1: excluded_reasons[rid][0] is already the dominant
        # (stage-A-first) code; this mirrors legacy mode's single-string
        # excluded_references, with the full tuple kept in `population`.
        excluded = {rid: reasons[0] for rid, reasons in descriptive.excluded_reasons.items()}
        scalar = (
            grade_scalar(observation.value, values, "higher_better")
            if observation.status is MetricStatus.AVAILABLE
            and observation.value is not None
            and values
            # StrEnum: compares equal to its own value, no runtime import needed.
            and descriptive.sufficiency == "SUFFICIENT_FOR_GRADING"
            and metric in _GRADABLE_METRICS
            else None
        )
        reasons: tuple[str, ...] = ()
        if scalar is not None and not scalar_is_finite(scalar):
            scalar, reasons = None, ("NONFINITE_DERIVED_STATISTIC",)
        comparisons[metric_id] = MetricComparison(
            metric_id,
            observation,
            used,
            tuple(values),
            excluded,
            scalar,
            reasons,
            population=population_set,
        )
    return comparisons
