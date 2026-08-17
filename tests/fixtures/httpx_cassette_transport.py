"""httpx-flavored recording/replay transports sharing the cassette format
from http_cassette.py.

WclClient (T0.3) and BlizzardClient (T0.4) use httpx, not requests — the
requests-based monkeypatch hooks in record.py's _RecordingHooks and
tests/conftest.py's mock_http fixture can't see their traffic at all.
These transports plug into the `transport=` constructor parameter both
clients already support for exactly this kind of dependency injection.
"""

from __future__ import annotations

import json as json_module
from typing import Any

import httpx
import pytest
from http_cassette import Cassette, cassette_key, load_cassette, redact_headers, save_cassette


def _extract_payload(request: httpx.Request) -> dict[str, Any] | None:
    if not request.content:
        return None
    try:
        return json_module.loads(request.content)
    except ValueError:
        # OAuth token endpoints use data=..., which httpx form-encodes
        # (application/x-www-form-urlencoded), not JSON.
        return dict(httpx.QueryParams(request.content.decode()))


class RecordingTransport(httpx.BaseTransport):
    """Writer side: makes the real call, saves a cassette, returns the
    real response. Response bodies are redacted by save_cassette()
    (docs/desvios.md D-6) before ever touching disk.
    """

    def __init__(self, real_transport: httpx.BaseTransport | None = None) -> None:
        self._real = real_transport or httpx.HTTPTransport()
        self.recorded = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._real.handle_request(request)
        response.read()
        payload = _extract_payload(request)
        try:
            body = response.json()
        except ValueError:
            body = None

        path = save_cassette(
            Cassette(
                method=request.method,
                url=str(request.url),
                request_payload=payload,
                request_headers=redact_headers(dict(request.headers)),
                status_code=response.status_code,
                response_json=body,
            )
        )
        self.recorded += 1
        print(f"  gravado: {path.name}  ({request.method} {request.url})")
        return response


class ReplayTransport(httpx.BaseTransport):
    """Reader side: matches cassettes by (method, url, payload) — same key
    format the requests-based mock_http fixture uses, so httpx- and
    requests-issued calls to the same logical request are interchangeable.
    """

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        payload = _extract_payload(request)
        key = cassette_key(request.method, str(request.url), payload)
        cassette = load_cassette(key)
        if cassette is None:
            pytest.fail(
                f"Cassete ausente para a chave '{key}' ({request.method} {request.url}).\n"
                f"Payload: {payload!r}\n"
                "Rode `python tests/fixtures/record.py` para (re)gravar as fixtures "
                "contra a API real (requer credenciais válidas em .env)."
            )
        return httpx.Response(cassette.status_code, json=cassette.response_json, request=request)
