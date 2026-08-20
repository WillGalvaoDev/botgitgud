"""T0.3 — robust WCL v2 GraphQL client: timeouts, retry/backoff, auth-token
expiry, and a rate-limit budget floor.

See docs/desvios.md D-7: takes a local `WclClientConfig` instead of the
`Settings` type the spec sketches, since `Settings` (pydantic-settings) is
only built in T1.1. Same public surface (`query()`, `points_remaining`).
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from botgitgud.errors import (
    AuthError,
    ConfigError,
    RateLimitBudgetExceeded,
    RateLimitCheckFailed,
    TransientApiError,
    WclGraphQLError,
)

log = structlog.get_logger(__name__)

API_URL = "https://www.warcraftlogs.com/api/v2/client"
TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"

# Codes that get a retry with exponential backoff (network/timeout errors are
# always retried regardless of this set — see _is_retryable_status).
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_NON_RETRYABLE_STATUS = frozenset({400, 401, 403, 404, 422})
_AUTH_STATUS = frozenset({401, 403})

_RATE_LIMIT_QUERY = "{ rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn } }"


@dataclass(frozen=True, slots=True)
class WclClientConfig:
    client_id: str
    client_secret: str
    connect_timeout: float = 5.0
    read_timeout: float = 30.0
    pool_timeout: float = 60.0
    max_attempts: int = 4  # 1 original + 3 retries
    backoff_base: float = 1.0
    backoff_factor: float = 2.0
    api_points_floor: float = 1000.0
    # How long a rate-limit-budget check is trusted before re-querying it.
    # Not a value the spec pins down explicitly (T1.8 owns the full budget/
    # fairness system) — a local, low-risk caching detail so query() doesn't
    # double its own API cost by rechecking the budget on every single call.
    rate_limit_cache_ttl: float = 60.0


def _is_retryable_status(status_code: int) -> bool:
    if status_code in _RETRYABLE_STATUS:
        return True
    if status_code in _NON_RETRYABLE_STATUS:
        return False
    # Unlisted codes: extrapolate by class (5xx transient, 4xx client error),
    # consistent with the given table's own split.
    return status_code >= 500


class WclClient:
    """Thread-safe enough for the ThreadPoolExecutor usage pattern in
    fetch_top_logs_for_cds-style code: each `query()` call is independent,
    and the only shared mutable state (token cache, rate-limit cache) is
    read-modify-write on plain attributes — acceptable here because the
    worst case is one redundant token/rate-limit refresh, never corrupted
    data. (Full thread-safety hardening, if needed, is Fase 1 territory.)
    """

    def __init__(
        self,
        config: WclClientConfig,
        transport: httpx.BaseTransport | None = None,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._clock = clock
        self._sleep = sleep
        self._client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(
                connect=config.connect_timeout,
                read=config.read_timeout,
                write=config.read_timeout,
                pool=config.pool_timeout,
            ),
        )
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._points_remaining: float | None = None
        self._points_limit: float | None = None
        self._points_reset_in: float | None = None
        self._rate_limit_checked_at: float = 0.0

    @property
    def points_remaining(self) -> float | None:
        if self._points_limit is None or self._points_remaining is None:
            return None
        return self._points_remaining

    @property
    def points_limit(self) -> float | None:
        return self._points_limit

    def refresh_budget(self) -> None:
        """T1.8: forces a rateLimitData check now, ignoring the cache TTL —
        never raises RateLimitBudgetExceeded (unlike _ensure_budget, called
        internally by query()); the job scheduler needs the raw numbers to
        decide which job types are currently allowed, not an exception. May
        still raise RateLimitCheckFailed if transport retries are exhausted
        (the check itself is unreachable) — a connectivity failure, not a
        budget-floor decision.
        """
        token = self._get_token()
        headers = {
            "Content-Type": "application/json",
            "Accept-Language": "en-US",
            "Authorization": f"Bearer {token}",
        }
        self._refresh_rate_limit(headers)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> WclClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- auth ---------------------------------------------------------------

    def _get_token(self) -> str:
        now = self._clock()
        if self._token is not None and now < self._token_expires_at:
            return self._token

        if not self._config.client_id or not self._config.client_secret:
            msg = "WCL client_id/client_secret ausentes na configuração"
            raise ConfigError(msg)

        res = self._client.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self._config.client_id, self._config.client_secret),
        )
        if res.status_code != 200:
            msg = f"falha ao autenticar na WCL: HTTP {res.status_code}"
            raise AuthError(msg)

        body = res.json()
        token = body.get("access_token")
        expires_in = body.get("expires_in", 0)
        if not token:
            msg = "resposta de token da WCL sem access_token"
            raise AuthError(msg)

        self._token = token
        self._token_expires_at = now + expires_in - 60
        return token

    # -- rate limit -----------------------------------------------------------

    def _refresh_rate_limit(self, headers: dict[str, str]) -> None:
        # Same retry/backoff as query() — an unretried transport failure here
        # used to crash the caller with a raw exception (fase4-experiment-
        # collection.md incident). Only connection-level failures retry; a
        # non-200 response keeps its original soft-fail path below.
        last_exc: httpx.TransportError | None = None
        for attempt in range(1, self._config.max_attempts + 1):
            try:
                res = self._client.post(API_URL, json={"query": _RATE_LIMIT_QUERY}, headers=headers)
            except httpx.TransportError as exc:
                last_exc = exc
                log.warning(
                    "wcl.rate_limit_refresh_transport_error", attempt=attempt, error=str(exc)
                )
                if attempt >= self._config.max_attempts:
                    break
                self._sleep(self._backoff_delay(attempt))
                continue
            self._apply_rate_limit_response(res)
            return
        # Fail closed: state genuinely unknown, never assume budget exists.
        attempts = self._config.max_attempts
        msg = f"orçamento de API indisponível após {attempts} tentativas: {last_exc}"
        raise RateLimitCheckFailed(msg) from last_exc

    def _apply_rate_limit_response(self, res: httpx.Response) -> None:
        if res.status_code != 200:
            # Non-fatal: query()'s own retry/timeout machinery surfaces a
            # real systemic outage; a single bad check shouldn't block.
            log.warning("wcl.rate_limit_check_failed", status=res.status_code)
            return
        data = (res.json().get("data") or {}).get("rateLimitData") or {}
        limit = data.get("limitPerHour")
        spent = data.get("pointsSpentThisHour")
        reset_in = data.get("pointsResetIn")
        if limit is None or spent is None:
            return
        self._points_limit = limit
        self._points_remaining = limit - spent
        self._points_reset_in = reset_in
        self._rate_limit_checked_at = self._clock()

    def _ensure_budget(self, headers: dict[str, str]) -> None:
        now = self._clock()
        stale = (now - self._rate_limit_checked_at) >= self._config.rate_limit_cache_ttl
        if stale or self._points_remaining is None:
            self._refresh_rate_limit(headers)

        if (
            self._points_remaining is not None
            and self._points_remaining < self._config.api_points_floor
        ):
            msg = (
                f"orçamento de API abaixo do piso: {self._points_remaining:.0f} pontos "
                f"restantes (piso {self._config.api_points_floor:.0f})"
            )
            raise RateLimitBudgetExceeded(
                msg,
                points_remaining=self._points_remaining,
                reset_in_seconds=self._points_reset_in or 0.0,
            )

    # -- query ----------------------------------------------------------------

    def query(
        self, query: str, variables: dict[str, Any] | None = None, *, op_name: str
    ) -> dict[str, Any]:
        token = self._get_token()
        headers = {
            "Content-Type": "application/json",
            "Accept-Language": "en-US",
            "Authorization": f"Bearer {token}",
        }

        self._ensure_budget(headers)

        last_exc: Exception | None = None
        for attempt in range(1, self._config.max_attempts + 1):
            start = self._clock()
            try:
                res = self._client.post(
                    API_URL, json={"query": query, "variables": variables or {}}, headers=headers
                )
            except httpx.TransportError as exc:
                last_exc = exc
                duration_ms = (self._clock() - start) * 1000
                log.warning(
                    "wcl.query_transport_error",
                    op_name=op_name,
                    attempt=attempt,
                    duration_ms=duration_ms,
                    error=str(exc),
                )
                if attempt >= self._config.max_attempts:
                    break
                self._sleep(self._backoff_delay(attempt))
                continue

            duration_ms = (self._clock() - start) * 1000
            log.info(
                "wcl.query",
                op_name=op_name,
                status=res.status_code,
                duration_ms=duration_ms,
                attempt=attempt,
            )

            if res.status_code == 200:
                body = res.json()
                errors = body.get("errors")
                if errors:
                    msg = f"WCL GraphQL error em '{op_name}': {errors}"
                    raise WclGraphQLError(msg, errors=errors)
                return body

            if res.status_code in _AUTH_STATUS:
                msg = f"'{op_name}' falhou com HTTP {res.status_code} (auth)"
                raise AuthError(msg)

            if not _is_retryable_status(res.status_code):
                msg = f"'{op_name}' falhou com HTTP {res.status_code} (não-retryable)"
                raise TransientApiError(msg)

            if attempt >= self._config.max_attempts:
                last_exc = TransientApiError(
                    f"'{op_name}' esgotou {self._config.max_attempts} tentativas "
                    f"(último status: {res.status_code})"
                )
                break

            delay = self._retry_after_seconds(res) or self._backoff_delay(attempt)
            self._sleep(delay)

        if isinstance(last_exc, TransientApiError):
            raise last_exc
        msg = f"'{op_name}' esgotou {self._config.max_attempts} tentativas"
        raise TransientApiError(msg) from last_exc

    def _backoff_delay(self, attempt: int) -> float:
        delay = self._config.backoff_base * (self._config.backoff_factor ** (attempt - 1))
        return random.uniform(0, delay)

    @staticmethod
    def _retry_after_seconds(res: httpx.Response) -> float | None:
        raw = res.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None
