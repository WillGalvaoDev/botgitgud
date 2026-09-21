"""Resolved, provenance-carrying identity for a WCL ability."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class IdentitySource(StrEnum):
    CURATED = "curated"
    WCL_REPORT_MASTER_DATA = "wcl_report_master_data"
    WCL_TABLE = "wcl_table"
    WCL_GAME_DATA = "wcl_game_data"
    BLIZZARD_GAME_DATA = "blizzard_game_data"
    LEGACY_CATALOG = "legacy_catalog"
    UNRESOLVED = "unresolved"


ResolutionStatus = Literal["resolved", "unresolved"]


@dataclass(frozen=True, slots=True)
class AbilityIdentity:
    canonical_id: int
    resolved_name: str
    identity_source: IdentitySource
    resolution_status: ResolutionStatus

    def __post_init__(self) -> None:
        if self.resolution_status not in ("resolved", "unresolved"):
            raise ValueError(f"invalid resolution status: {self.resolution_status!r}")
        unresolved_status = self.resolution_status == "unresolved"
        unresolved_source = self.identity_source is IdentitySource.UNRESOLVED
        empty_name = self.resolved_name == ""
        if not (unresolved_status == unresolved_source == empty_name):
            raise ValueError(
                "unresolved status, UNRESOLVED source, and an empty name must coincide"
            )
