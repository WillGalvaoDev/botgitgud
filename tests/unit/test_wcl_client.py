from __future__ import annotations

import json

import httpx
import pytest

from botgitgud.errors import (
    AuthError,
    ConfigError,
    RateLimitBudgetExceeded,
    RateLimitCheckFailed,
    TransientApiError,
    WclGraphQLError,
)
from botgitgud.wcl.client import _RATE_LIMIT_QUERY, API_URL, TOKEN_URL, WclClient, WclClientConfig


def _token_response(expires_in: int = 3600) -> httpx.Response:
    return httpx.Response(
        200, json={"access_token": "fake-token", "expires_in": expires_in, "token_type": "Bearer"}
    )


def _rate_limit_ok_response(remaining: float = 3600.0) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": {
                "rateLimitData": {
                    "limitPerHour": remaining,
                    "pointsSpentThisHour": 0,
                    "pointsResetIn": 3600,
                }
            }
        },
    )


def _is_rate_limit_query(request: httpx.Request) -> bool:
    body = json.loads(request.content)
    return body.get("query") == _RATE_LIMIT_QUERY


def _config(**overrides: object) -> WclClientConfig:
    defaults: dict[str, object] = {"client_id": "id", "client_secret": "secret"}
    defaults.update(overrides)
    return WclClientConfig(**defaults)  # type: ignore[arg-type]


def test_retries_429_with_retry_after_then_succeeds() -> None:
    counts = {"real_query": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(TOKEN_URL):
            return _token_response()
        if _is_rate_limit_query(request):
            return _rate_limit_ok_response()
        counts["real_query"] += 1
        if counts["real_query"] <= 2:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return httpx.Response(200, json={"data": {"ok": True}})

    client = WclClient(_config(), transport=httpx.MockTransport(handler), sleep=sleeps.append)

    result = client.query("query { x }", {}, op_name="test_op")

    assert result == {"data": {"ok": True}}
    assert counts["real_query"] == 3
    assert len(sleeps) == 2


def test_401_raises_auth_error_without_retry() -> None:
    counts = {"real_query": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(TOKEN_URL):
            return _token_response()
        if _is_rate_limit_query(request):
            return _rate_limit_ok_response()
        counts["real_query"] += 1
        return httpx.Response(401, json={})

    client = WclClient(_config(), transport=httpx.MockTransport(handler), sleep=lambda _s: None)

    with pytest.raises(AuthError):
        client.query("query { x }", {}, op_name="test_op")

    assert counts["real_query"] == 1


def test_token_refetched_after_expiry_with_injected_clock() -> None:
    token_calls = {"n": 0}
    clock_value = {"t": 0.0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(TOKEN_URL):
            token_calls["n"] += 1
            return _token_response(expires_in=100)
        if _is_rate_limit_query(request):
            return _rate_limit_ok_response()
        return httpx.Response(200, json={"data": {"ok": True}})

    client = WclClient(
        _config(),
        transport=httpx.MockTransport(handler),
        clock=lambda: clock_value["t"],
        sleep=lambda _s: None,
    )

    client.query("query { x }", {}, op_name="op1")
    assert token_calls["n"] == 1

    clock_value["t"] = 41.0  # past expires_at = 0 + 100 - 60 = 40
    client.query("query { x }", {}, op_name="op2")
    assert token_calls["n"] == 2


def test_graphql_errors_field_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(TOKEN_URL):
            return _token_response()
        if _is_rate_limit_query(request):
            return _rate_limit_ok_response()
        return httpx.Response(200, json={"errors": [{"message": "boom"}]})

    client = WclClient(_config(), transport=httpx.MockTransport(handler), sleep=lambda _s: None)

    with pytest.raises(WclGraphQLError) as exc_info:
        client.query("query { x }", {}, op_name="op")

    assert exc_info.value.errors == [{"message": "boom"}]


def test_missing_credentials_raises_config_error() -> None:
    client = WclClient(
        _config(client_id="", client_secret=""),
        transport=httpx.MockTransport(lambda _r: _token_response()),
        sleep=lambda _s: None,
    )

    with pytest.raises(ConfigError):
        client.query("query { x }", {}, op_name="op")


def test_rate_limit_budget_below_floor_raises_before_real_call() -> None:
    counts = {"real_query": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(TOKEN_URL):
            return _token_response()
        if _is_rate_limit_query(request):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "rateLimitData": {
                            "limitPerHour": 3600,
                            "pointsSpentThisHour": 3000,  # only 600 remaining
                            "pointsResetIn": 120,
                        }
                    }
                },
            )
        counts["real_query"] += 1
        return httpx.Response(200, json={"data": {"ok": True}})

    client = WclClient(
        _config(api_points_floor=1000.0),
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )

    with pytest.raises(RateLimitBudgetExceeded) as exc_info:
        client.query("query { x }", {}, op_name="op")

    assert exc_info.value.points_remaining == 600
    assert counts["real_query"] == 0  # never even attempted the real call


def test_transport_error_retries_then_raises_transient() -> None:
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(TOKEN_URL):
            return _token_response()
        if _is_rate_limit_query(request):
            return _rate_limit_ok_response()
        raise httpx.ConnectError("boom", request=request)

    client = WclClient(_config(), transport=httpx.MockTransport(handler), sleep=sleeps.append)

    with pytest.raises(TransientApiError):
        client.query("query { x }", {}, op_name="op")

    assert len(sleeps) == 3  # 3 retries after the first failed attempt


def test_points_limit_and_remaining_populated_after_a_query() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(TOKEN_URL):
            return _token_response()
        if _is_rate_limit_query(request):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "rateLimitData": {
                            "limitPerHour": 3600,
                            "pointsSpentThisHour": 100,
                            "pointsResetIn": 3500,
                        }
                    }
                },
            )
        return httpx.Response(200, json={"data": {"ok": True}})

    client = WclClient(_config(), transport=httpx.MockTransport(handler), sleep=lambda _s: None)
    assert client.points_limit is None  # nothing queried yet

    client.query("query { x }", {}, op_name="op")

    assert client.points_limit == 3600
    assert client.points_remaining == 3500


def test_refresh_budget_never_raises_even_below_floor() -> None:
    """T1.8: the job scheduler needs raw numbers to decide policy, not an
    exception — unlike _ensure_budget (exercised via query()).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(TOKEN_URL):
            return _token_response()
        if _is_rate_limit_query(request):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "rateLimitData": {
                            "limitPerHour": 3600,
                            "pointsSpentThisHour": 3200,  # only 400 remaining
                            "pointsResetIn": 60,
                        }
                    }
                },
            )
        return httpx.Response(200, json={"data": {"ok": True}})

    client = WclClient(
        _config(api_points_floor=1000.0),
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )

    client.refresh_budget()  # must not raise

    assert client.points_remaining == 400
    assert client.points_limit == 3600


# -- rate-limit refresh: transport-fault injection ---------------------------
# Reproduces the incident: a transient network failure during the periodic
# budget refresh (never the main query itself) used to propagate a raw
# httpx.TransportError straight out of query() and crash the caller
# (docs/phase4.md).


@pytest.mark.parametrize(
    "transport_error", [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError]
)
def test_rate_limit_refresh_retries_transient_transport_error_then_succeeds(
    transport_error: type[httpx.TransportError],
) -> None:
    counts = {"refresh": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(TOKEN_URL):
            return _token_response()
        if _is_rate_limit_query(request):
            counts["refresh"] += 1
            if counts["refresh"] == 1:
                raise transport_error("boom", request=request)
            return _rate_limit_ok_response()
        return httpx.Response(200, json={"data": {"ok": True}})

    client = WclClient(_config(), transport=httpx.MockTransport(handler), sleep=sleeps.append)

    result = client.query("query { x }", {}, op_name="op")

    assert result == {"data": {"ok": True}}
    assert counts["refresh"] == 2  # one failure, one successful retry
    assert len(sleeps) == 1


def test_rate_limit_refresh_exhausted_retries_fails_closed_before_real_call() -> None:
    counts = {"refresh": 0, "real_query": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(TOKEN_URL):
            return _token_response()
        if _is_rate_limit_query(request):
            counts["refresh"] += 1
            raise httpx.ConnectTimeout("boom", request=request)
        counts["real_query"] += 1
        return httpx.Response(200, json={"data": {"ok": True}})

    client = WclClient(_config(), transport=httpx.MockTransport(handler), sleep=sleeps.append)

    with pytest.raises(RateLimitCheckFailed):
        client.query("query { x }", {}, op_name="op")

    assert counts["refresh"] == 4  # default max_attempts, no infinite retry
    assert counts["real_query"] == 0  # fail closed: never assumed budget existed
    assert len(sleeps) == 3


def test_rate_limit_refresh_retry_count_respects_configured_max_attempts() -> None:
    counts = {"refresh": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(TOKEN_URL):
            return _token_response()
        if _is_rate_limit_query(request):
            counts["refresh"] += 1
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json={"data": {"ok": True}})

    client = WclClient(
        _config(max_attempts=2), transport=httpx.MockTransport(handler), sleep=lambda _s: None
    )

    with pytest.raises(RateLimitCheckFailed):
        client.query("query { x }", {}, op_name="op")

    assert counts["refresh"] == 2  # reuses the project's own configured limit


def test_refresh_budget_fails_closed_after_exhausted_transport_retries() -> None:
    """refresh_budget() gains the same protection as query() — previously a
    transport failure here had zero handling and crashed uncaught.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(TOKEN_URL):
            return _token_response()
        if _is_rate_limit_query(request):
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json={"data": {"ok": True}})

    client = WclClient(_config(), transport=httpx.MockTransport(handler), sleep=lambda _s: None)

    with pytest.raises(RateLimitCheckFailed):
        client.refresh_budget()


def test_rate_limit_refresh_non_200_status_keeps_original_soft_fail_untouched() -> None:
    """This fix only adds retries for transport-level failures — a non-200
    HTTP response must keep its pre-existing, deliberately soft-fail path
    (no retry, query proceeds), unchanged.
    """
    counts = {"refresh": 0, "real_query": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(TOKEN_URL):
            return _token_response()
        if _is_rate_limit_query(request):
            counts["refresh"] += 1
            return httpx.Response(500, json={})
        counts["real_query"] += 1
        return httpx.Response(200, json={"data": {"ok": True}})

    client = WclClient(_config(), transport=httpx.MockTransport(handler), sleep=lambda _s: None)

    result = client.query("query { x }", {}, op_name="op")

    assert result == {"data": {"ok": True}}
    assert counts["refresh"] == 1  # not retried — different, pre-existing code path
    assert counts["real_query"] == 1  # proceeded, exactly like before this fix


def test_client_uses_expected_wcl_endpoint() -> None:
    """Sanity check the module points at the real WCL v2 endpoint."""
    assert API_URL == "https://www.warcraftlogs.com/api/v2/client"
    assert TOKEN_URL == "https://www.warcraftlogs.com/oauth/token"
