"""Fail-closed guard for quantitative damage consumers."""

from collections.abc import Sequence

from botgitgud.domain.damage_scope import DamageScopeVersion
from botgitgud.domain.models import PlayerLog


class MixedDamageScopeError(ValueError):
    """Damage logs cannot safely participate in the same statistic."""


def require_homogeneous_scope(logs: Sequence[PlayerLog]) -> DamageScopeVersion:
    """Return the common eligible scope version or fail closed."""
    versions = {log.damage_scope for log in logs}
    if versions & {DamageScopeVersion.UNRECONCILED}:
        raise MixedDamageScopeError("unreconciled damage is not quantitatively eligible")
    try:
        (version,) = versions
    except ValueError as error:
        raise MixedDamageScopeError("quantitative damage scopes are mixed") from error
    return version


def select_comparable(
    player_log: PlayerLog, others: Sequence[PlayerLog]
) -> tuple[tuple[PlayerLog, ...], int]:
    """Select logs governed by the player's damage-population contract.

    This is the report-path counterpart to :func:`require_homogeneous_scope`:
    heterogeneous references are excluded from this one quantitative metric
    instead of aborting unrelated report features.
    """
    by_scope: dict[DamageScopeVersion, list[PlayerLog]] = {}
    for log in others:
        by_scope.setdefault(log.damage_scope, []).append(log)
    selected = tuple(by_scope.get(player_log.damage_scope, ()))
    return selected, len(others) - len(selected)
