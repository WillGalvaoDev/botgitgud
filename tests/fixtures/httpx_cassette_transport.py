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
        # Production readiness raised Casts from 5k to 10k. Historical
        # immutable cassettes are still a valid semantic replay: their
        # nextPageTimestamp chain reconstructs the same complete event set.
        # Do not re-record against WCL merely because a query literal changed.
        if cassette is None and isinstance(payload, dict):
            query = payload.get("query")
            # M16 adds report masterData abilities to GetPlayerMeta. Historical
            # responses do not contain that field, so replay identity comes only
            # from the secondary tables already present in those responses.
            if (
                isinstance(query, str)
                and "GetPlayerMeta" in query
                and "        abilities { gameID name icon type }\n" in query
            ):
                legacy_query = query.replace(
                    "        abilities { gameID name icon type }\n", ""
                ).replace(
                    "      masterData {\n        actors { id name type subType petOwner }\n      }",
                    "      masterData { actors { id name type subType petOwner } }",
                )
                legacy_payload = {**payload, "query": legacy_query}
                legacy_key = cassette_key(request.method, str(request.url), legacy_payload)
                cassette = load_cassette(legacy_key)
            if isinstance(query, str) and "limit: 10000" in query and "dataType: Casts" in query:
                legacy_payload = {**payload, "query": query.replace("limit: 10000", "limit: 5000")}
                legacy_key = cassette_key(request.method, str(request.url), legacy_payload)
                cassette = load_cassette(legacy_key)
            # M3 compatibility is intentionally Mythic-only.  The recorded golden
            # target is difficulty=5 and the live measurement showed that the old,
            # unfiltered query returned Mythic, so that one replay is equivalent.
            # Heroic (or any other difficulty) fails closed.  GATE-10 is resolved:
            # a same-instant measurement found difficulty=5 and the unfiltered query
            # identical; full re-recording would require the wider dependent-log set.
            variables = payload.get("variables")
            if (
                cassette is None
                and isinstance(query, str)
                and "GetRankingsCDs" in query
                and isinstance(variables, dict)
                and variables.get("difficulty") == 5
            ):
                legacy_query = query.replace(
                    "$encounterID: Int!, $className: String!, $specName: String!, "
                    "$page: Int!, $partition: Int!,\n"
                    "  $difficulty: Int!",
                    "$encounterID: Int!, $className: String!, $specName: String!, "
                    "$page: Int!, $partition: Int!",
                ).replace(
                    "className: $className, specName: $specName, metric: dps, page: $page,\n"
                    "        partition: $partition, difficulty: $difficulty",
                    "className: $className, specName: $specName, metric: dps, "
                    "page: $page, partition: $partition",
                )
                legacy_payload = {
                    **payload,
                    "query": legacy_query,
                    "variables": {k: v for k, v in variables.items() if k != "difficulty"},
                }
                legacy_key = cassette_key(request.method, str(request.url), legacy_payload)
                cassette = load_cassette(legacy_key)
        if cassette is None:
            pytest.fail(
                f"Cassete ausente para a chave '{key}' ({request.method} {request.url}).\n"
                f"Payload: {payload!r}\n"
                "Rode `python tests/fixtures/record.py` para (re)gravar as fixtures "
                "contra a API real (requer credenciais válidas em .env)."
            )
        return httpx.Response(cassette.status_code, json=cassette.response_json, request=request)
