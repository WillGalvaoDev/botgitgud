"""Shared pytest fixtures.

`mock_http` replays tests/fixtures/cassettes/*.json in place of real network
calls. It patches the module-level `requests.post`/`requests.get` that
legacy/bot.py (and, in later tasks, other code exercised against recorded
traffic) resolves at call time — see tests/fixtures/http_cassette.py for the
key format shared with the recorder (tests/fixtures/record.py).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from http_cassette import cassette_key, load_cassette
from synthetic import build_synthetic_cohort, build_synthetic_user_timeline


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = "" if payload is None else str(payload)

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            msg = f"HTTP {self.status_code}"
            raise requests.HTTPError(msg)


def _replay(method: str, url: str, payload: dict[str, Any] | None) -> _FakeResponse:
    key = cassette_key(method, url, payload)
    cassette = load_cassette(key)
    if cassette is None:
        pytest.fail(
            f"Cassete ausente para a chave '{key}' ({method} {url}).\n"
            f"Payload: {payload!r}\n"
            "Rode `python tests/fixtures/record.py` para (re)gravar as fixtures "
            "contra a API real (requer credenciais válidas em .env)."
        )
    return _FakeResponse(cassette.status_code, cassette.response_json)


@pytest.fixture
def mock_http(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(
        url: str,
        *,
        data: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        timeout: float | None = None,
        **_kw: Any,
    ) -> _FakeResponse:
        return _replay("POST", url, json if json is not None else data)

    def fake_get(
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        **_kw: Any,
    ) -> _FakeResponse:
        return _replay("GET", url, params)

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)


@pytest.fixture
def synthetic_user_timeline() -> dict[int, list[float]]:
    """3 hand-picked abilities; see tests/fixtures/synthetic.py for exact values."""
    return build_synthetic_user_timeline()


@pytest.fixture
def synthetic_cohort() -> list[dict[str, Any]]:
    """10 reference players with known presence/variance; see tests/fixtures/synthetic.py."""
    return build_synthetic_cohort()
