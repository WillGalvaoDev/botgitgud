"""Strict, role-specific wire contract. Free text is never a transition."""

from __future__ import annotations

import json
from typing import Any


class Blocked(ValueError):
    """An operational or protocol precondition failed closed."""


ROLES = {
    "opus": {"SPEC_READY", "HUMAN_BLOCK"},
    "sonnet": {"IMPLEMENTATION_READY", "TECH_BLOCK", "HUMAN_BLOCK"},
    "astra": {"MILESTONE_CLOSED", "REQUIRES_CHANGES", "HUMAN_BLOCK"},
}
STATUSES = sorted(set().union(*ROLES.values()))


def object_schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


STRING = {"type": "string"}


def array(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


SCHEMA = object_schema(
    {
        "status": {"type": "string", "enum": STATUSES},
        "unit": STRING,
        "spec_sha": STRING,
        "reason": STRING,
        "route": {"type": "string", "enum": ["", "implementation", "specification"]},
        "spec": STRING,
        "criteria": array(object_schema({"id": STRING, "text": STRING})),
        "write_paths": array(STRING),
        "files": array(object_schema({"path": STRING, "content": STRING})),
        "evidence": array(
            object_schema(
                {
                    "criterion": STRING,
                    "test": STRING,
                    "inputs": STRING,
                    "expected": STRING,
                    "observed": STRING,
                    "artifact": STRING,
                }
            )
        ),
        "findings": array(
            object_schema(
                {
                    "basis": {"type": "string", "enum": ["criterion", "invariant", "debt"]},
                    "reference": STRING,
                    "counterexample": STRING,
                }
            )
        ),
    }
)


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise Blocked(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json(raw: str) -> Any:
    try:
        return json.loads(
            raw,
            object_pairs_hook=unique_object,
            parse_constant=lambda value: reject(f"Nonfinite JSON: {value}"),
        )
    except (ValueError, TypeError) as exc:
        raise Blocked(f"Invalid JSON protocol: {exc}") from exc


def reject(message: str) -> Any:
    raise Blocked(message)


def validate_shape(value: Any, schema: dict[str, Any]) -> None:
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict) or set(value) != set(schema["required"]):
            raise Blocked("Missing or unexpected protocol fields")
        for key, child in schema["properties"].items():
            validate_shape(value[key], child)
    elif kind == "array":
        if not isinstance(value, list):
            raise Blocked("Expected array")
        for item in value:
            validate_shape(item, schema["items"])
    elif not isinstance(value, str):
        raise Blocked("Expected string")
    if "enum" in schema and value not in schema["enum"]:
        raise Blocked("Unknown protocol token")


def parse(raw: str, role: str, unit: str, spec_sha: str) -> dict[str, Any]:
    value = strict_json(raw)
    validate_shape(value, SCHEMA)
    if value["status"] not in ROLES[role] or value["unit"] != unit:
        raise Blocked("Wrong role status or unit")
    if value["spec_sha"] != spec_sha:
        raise Blocked("Response is not bound to the current SPEC")
    if not value["reason"].strip():
        raise Blocked("Empty result reason")
    status = value["status"]
    # All unused payload fields must be empty; contradictory payloads cannot advance.
    permitted = {
        "SPEC_READY": {"spec", "criteria", "write_paths"},
        "IMPLEMENTATION_READY": {"files", "evidence"},
        "REQUIRES_CHANGES": {"route", "findings"},
        "MILESTONE_CLOSED": {"findings"},
        "HUMAN_BLOCK": set(),
        "TECH_BLOCK": set(),
    }[status]
    for field in {"route", "spec", "criteria", "write_paths", "files", "evidence", "findings"}:
        if field not in permitted and value[field]:
            raise Blocked(f"Contradictory {status} payload: {field}")
    if status == "SPEC_READY":
        ids = [c["id"] for c in value["criteria"]]
        if (
            not value["spec"].strip()
            or not 3 <= len(ids) <= 7
            or len(set(ids)) != len(ids)
            or not value["write_paths"]
            or any(not c["id"].strip() or not c["text"].strip() for c in value["criteria"])
        ):
            raise Blocked("Incomplete SPEC contract")
    if status == "IMPLEMENTATION_READY" and (not value["files"] or not value["evidence"]):
        raise Blocked("Empty implementation/evidence")
    if status == "REQUIRES_CHANGES":
        blockers = [f for f in value["findings"] if f["basis"] != "debt"]
        if not value["route"] or not blockers:
            raise Blocked("Correction must identify routing and a contractual blocker")
    if status == "MILESTONE_CLOSED" and any(f["basis"] != "debt" for f in value["findings"]):
        raise Blocked("Closure contradicts blocking findings")
    for finding in value["findings"]:
        if not finding["reference"].strip() or not finding["counterexample"].strip():
            raise Blocked("Finding lacks reference or counterexample")
    return value
