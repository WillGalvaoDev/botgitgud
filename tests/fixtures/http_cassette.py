"""Shared cassette key/format logic used by both record.py (writer) and
conftest.py's mock_http fixture (reader). Kept as one module so the two
sides can never compute the key differently and silently miss each other.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CASSETTES_DIR = Path(__file__).resolve().parent / "cassettes"


def cassette_key(method: str, url: str, payload: dict[str, Any] | None) -> str:
    body = json.dumps(payload or {}, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(f"{method}:{url}:{body}".encode()).hexdigest()
    return digest[:16]


def redact_headers(headers: dict[str, str] | None) -> dict[str, str]:
    if not headers:
        return {}
    return {k: ("<redacted>" if k.lower() == "authorization" else v) for k, v in headers.items()}


_SECRET_RESPONSE_KEYS = {"access_token", "refresh_token", "id_token"}


def redact_response_body(body: Any) -> Any:
    """OAuth token endpoints return the actual credential IN the response body,
    not just in a header. A cassette must never carry a real, usable token —
    this one is valid for ~1 year (see docs/desvios.md D-6). The redacted
    value only needs to be *some* string: replay matches cassettes by
    (method, url, request payload), never by header/token content, so a
    placeholder round-trips correctly through the rest of the pipeline.
    """
    if isinstance(body, dict):
        return {
            k: ("<redacted-fixture-token>" if k in _SECRET_RESPONSE_KEYS else v)
            for k, v in body.items()
        }
    return body


@dataclass(frozen=True, slots=True)
class Cassette:
    method: str
    url: str
    request_payload: dict[str, Any] | None
    request_headers: dict[str, str]
    status_code: int
    response_json: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "url": self.url,
            "request_payload": self.request_payload,
            "request_headers": self.request_headers,
            "status_code": self.status_code,
            "response_json": self.response_json,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Cassette:
        return Cassette(
            method=data["method"],
            url=data["url"],
            request_payload=data.get("request_payload"),
            request_headers=data.get("request_headers") or {},
            status_code=data["status_code"],
            response_json=data.get("response_json"),
        )


def cassette_path(key: str) -> Path:
    return CASSETTES_DIR / f"{key}.json"


def save_cassette(cassette: Cassette) -> Path:
    key = cassette_key(cassette.method, cassette.url, cassette.request_payload)
    CASSETTES_DIR.mkdir(parents=True, exist_ok=True)
    path = cassette_path(key)
    sanitized = Cassette(
        method=cassette.method,
        url=cassette.url,
        request_payload=cassette.request_payload,
        request_headers=cassette.request_headers,
        status_code=cassette.status_code,
        response_json=redact_response_body(cassette.response_json),
    )
    path.write_text(
        json.dumps(sanitized.to_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return path


def load_cassette(key: str) -> Cassette | None:
    path = cassette_path(key)
    if not path.exists():
        return None
    return Cassette.from_dict(json.loads(path.read_text(encoding="utf-8")))
