from __future__ import annotations

import httpx

from botgitgud.blizzard.client import BlizzardClient, BlizzardClientConfig


def _config(**overrides: object) -> BlizzardClientConfig:
    defaults: dict[str, object] = {"client_id": "id", "client_secret": "secret"}
    defaults.update(overrides)
    return BlizzardClientConfig(**defaults)  # type: ignore[arg-type]


def test_client_constructs_without_error() -> None:
    """Regression test: httpx.Timeout(connect=..., read=...) without write/pool
    raises ValueError unless a default is also given — this slipped through
    T0.4 because every existing test used a fake stub instead of the real
    class, and only got caught wiring the real thing in T0.7.
    """
    client = BlizzardClient(_config())
    client.close()


def _token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})


def test_get_spell_name_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth.battle.net" in str(request.url):
            return _token_response()
        return httpx.Response(200, json={"id": 133, "name": "Fireball"})

    client = BlizzardClient(
        _config(), transport=httpx.MockTransport(handler), sleep=lambda _s: None
    )
    assert client.get_spell_name(133) == "Fireball"


def test_get_spell_name_404_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth.battle.net" in str(request.url):
            return _token_response()
        return httpx.Response(404, json={})

    client = BlizzardClient(
        _config(), transport=httpx.MockTransport(handler), sleep=lambda _s: None
    )
    assert client.get_spell_name(999999) is None


def test_get_spell_name_missing_credentials_returns_none_not_raise() -> None:
    client = BlizzardClient(
        _config(client_id="", client_secret=""),
        transport=httpx.MockTransport(lambda _r: _token_response()),
        sleep=lambda _s: None,
    )
    assert client.get_spell_name(1) is None


def test_get_spell_name_retries_5xx_then_succeeds() -> None:
    counts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth.battle.net" in str(request.url):
            return _token_response()
        counts["n"] += 1
        if counts["n"] == 1:
            return httpx.Response(503, json={})
        return httpx.Response(200, json={"id": 1, "name": "Frostbolt"})

    client = BlizzardClient(
        _config(), transport=httpx.MockTransport(handler), sleep=lambda _s: None
    )
    assert client.get_spell_name(1) == "Frostbolt"
    assert counts["n"] == 2
