"""M31: conservative cross-kind ordering of material coaching candidates.

The ordering is precedence, not arithmetic: discrete material band first,
then semantic layer.  Only ABILITY_GAP candidates are compared by their
within-kind measured gain.  Everything still tied is ordered by a declared
arbitrary lexical key so arrival order cannot change the result.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from enum import StrEnum
from functools import cmp_to_key

from botgitgud.analysis.findings import ExecutionFinding, Finding
from botgitgud.analysis.materiality import MaterialCandidate


class SemanticLayer(StrEnum):
    PARTICIPATION_LOSS = "PARTICIPATION_LOSS"
    OUTPUT_DEFICIT = "OUTPUT_DEFICIT"


_BAND_ORDER = ("red", "yellow")
_LAYER_ORDER = (SemanticLayer.PARTICIPATION_LOSS, SemanticLayer.OUTPUT_DEFICIT)


def _kind(candidate: MaterialCandidate) -> str:
    finding = candidate.finding
    return finding.category if isinstance(finding, ExecutionFinding) else finding.kind


def _layer(candidate: MaterialCandidate) -> SemanticLayer:
    return (
        SemanticLayer.PARTICIPATION_LOSS
        if _kind(candidate) in {"DEATH", "ACTIVE_TIME"}
        else SemanticLayer.OUTPUT_DEFICIT
    )


def _stable_key(candidate: MaterialCandidate) -> tuple[str, str, str, str]:
    """A deterministic, explicitly non-semantic final tie-break."""
    finding = candidate.finding
    subject = candidate.remediation.subject
    if isinstance(finding, ExecutionFinding):
        # The authoritative execution producer emits one DEATH, one
        # ACTIVE_TIME, and at most one WASTE per resource subject. No measured
        # magnitude is needed (or allowed) in the arbitrary tie-break.
        description = ""
    else:
        description = f"{finding.title}:{finding.detail}"
    return (_kind(candidate), type(subject).__name__, str(subject), description)


def _compare(left: MaterialCandidate, right: MaterialCandidate) -> int:
    left_band = _BAND_ORDER.index(left.grade)
    right_band = _BAND_ORDER.index(right.grade)
    if left_band != right_band:
        return -1 if left_band < right_band else 1

    left_layer = _LAYER_ORDER.index(_layer(left))
    right_layer = _LAYER_ORDER.index(_layer(right))
    if left_layer != right_layer:
        return -1 if left_layer < right_layer else 1

    left_kind = _kind(left)
    right_kind = _kind(right)
    if left_kind == right_kind == "ABILITY_GAP":
        left_finding = left.finding
        right_finding = right.finding
        assert isinstance(left_finding, Finding)
        assert isinstance(right_finding, Finding)
        left_gain = left_finding.estimated_gain_pct
        right_gain = right_finding.estimated_gain_pct
        if left_gain is not None and right_gain is not None and left_gain != right_gain:
            return -1 if left_gain > right_gain else 1

    left_stable = _stable_key(left)
    right_stable = _stable_key(right)
    if left_stable == right_stable:
        return 0
    return -1 if left_stable < right_stable else 1


def _same_evidence_precedence(left: MaterialCandidate, right: MaterialCandidate) -> bool:
    """Whether evidence does not order two candidates before the stable key."""
    if left.grade != right.grade or _layer(left) is not _layer(right):
        return False
    left_kind = _kind(left)
    right_kind = _kind(right)
    if left_kind != right_kind:
        return True
    if left_kind != "ABILITY_GAP":
        return True
    left_finding = left.finding
    right_finding = right.finding
    assert isinstance(left_finding, Finding)
    assert isinstance(right_finding, Finding)
    return left_finding.estimated_gain_pct == right_finding.estimated_gain_pct


def select_material_priorities(
    candidates: Sequence[MaterialCandidate],
) -> tuple[MaterialCandidate, ...]:
    """Return zero to three candidates according to RB-2's exact order."""
    ordered = sorted(candidates, key=cmp_to_key(_compare))
    marked = (
        replace(
            candidate,
            ordering_is_arbitrary=sum(
                _same_evidence_precedence(candidate, other) for other in ordered
            )
            > 1,
        )
        for candidate in ordered[:3]
    )
    return tuple(marked)
