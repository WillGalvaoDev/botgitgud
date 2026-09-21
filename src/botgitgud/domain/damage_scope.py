"""Versioned semantics for damage-event populations."""

from enum import StrEnum


class DamageScopeVersion(StrEnum):
    """The population contract used by a log's quantitative damage data."""

    LEGACY_UNSCOPED = "legacy_unscoped"
    WCL_TARGET_SCOPE_V1 = "wcl_target_scope_v1"
    UNRECONCILED = "unreconciled"
