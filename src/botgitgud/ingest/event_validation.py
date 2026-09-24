"""Validate the event fields required by damage/cast parsers."""

import math


def valid_event(
    event: object, *, start_time_ms: float | None = None, end_time_ms: float | None = None
) -> bool:
    if not isinstance(event, dict):
        return False
    kind = event.get("type")
    if not isinstance(kind, str) or not kind:
        return False
    if kind not in {"damage", "cast"}:
        return True  # Other streams have separate contracts.
    required = (
        ("sourceID", "abilityGameID", "timestamp")
        if kind == "cast"
        else ("sourceID", "abilityGameID", "targetID")
    )
    for key in required:
        value = event.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if not math.isfinite(value) or (key != "abilityGameID" and value < 0):
            return False
        if key != "timestamp" and value != int(value):
            return False
    timestamp = event.get("timestamp")
    if (
        start_time_ms is not None
        and end_time_ms is not None
        and (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float))
            or not math.isfinite(timestamp)
            or not start_time_ms <= timestamp <= end_time_ms
        )
    ):
        return False
    if kind == "damage":
        value = event.get("amount")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if not math.isfinite(value) or value < 0:
            return False
        absorbed = event.get("absorbed", 0)
        if absorbed is not None and (
            isinstance(absorbed, bool)
            or not isinstance(absorbed, (int, float))
            or not math.isfinite(absorbed)
            or absorbed < 0
        ):
            return False
        if event.get("subtractsFromSupportedActor"):
            support = event.get("supportID")
            if (
                isinstance(support, bool)
                or not isinstance(support, (int, float))
                or not math.isfinite(support)
                or support < 0
                or support != int(support)
            ):
                return False
    return True
