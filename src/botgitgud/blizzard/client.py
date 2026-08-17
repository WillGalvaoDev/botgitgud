"""Minimal, robust Blizzard API client — spell-name lookup only.

See docs/desvios.md D-8: no task in the 27-task plan explicitly builds this
module, even though T0.4's SpellCatalog signature requires a BlizzardClient.
Built here, scoped to exactly what SpellCatalog needs (one lookup method),
following the same robustness pattern as wcl/client.py (T0.3): timeouts,
retry/backoff on transient failures, OAuth token cached with real expiry.
No rate-limit-budget floor here — that concept is WCL-specific
(rateLimitData); Blizzard's API doesn't expose an equivalent field.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import structlog

from botgitgud.errors import AuthError, ConfigError

log = structlog.get_logger(__name__)

TOKEN_URL = "https://oauth.battle.net/token"
SPELL_URL_TEMPLATE = "https://us.api.blizzard.com/data/wow/spell/{spell_id}"

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class BlizzardClientConfig:
    client_id: str
    client_secret: str
    connect_timeout: float = 5.0
    read_timeout: float = 10.0
    max_attempts: int = 3  # 1 original + 2 retries — spell lookups are best-effort
    backoff_base: float = 1.0
    backoff_factor: float = 2.0
    namespace: str = "static-us"
    locale: str = "en_US"


class BlizzardClient:
    def __init__(
        self,
        config: BlizzardClientConfig,
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
            timeout=httpx.Timeout(config.read_timeout, connect=config.connect_timeout),
        )
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> BlizzardClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _get_token(self) -> str:
        now = self._clock()
        if self._token is not None and now < self._token_expires_at:
            return self._token

        if not self._config.client_id or not self._config.client_secret:
            msg = "Blizzard client_id/client_secret ausentes na configuração"
            raise ConfigError(msg)

        res = self._client.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self._config.client_id, self._config.client_secret),
        )
        if res.status_code != 200:
            msg = f"falha ao autenticar na Blizzard: HTTP {res.status_code}"
            raise AuthError(msg)

        body = res.json()
        token = body.get("access_token")
        expires_in = body.get("expires_in", 0)
        if not token:
            msg = "resposta de token da Blizzard sem access_token"
            raise AuthError(msg)

        self._token = token
        self._token_expires_at = now + expires_in - 60
        return token

    def get_spell_name(self, spell_id: int) -> str | None:
        """Best-effort spell name lookup. Returns None on any failure
        (unknown spell, auth/config issue, exhausted retries) — callers
        (SpellCatalog) treat that as "name unknown", never a hard failure.
        """
        try:
            token = self._get_token()
        except (AuthError, ConfigError) as exc:
            log.warning("blizzard.auth_failed", error=str(exc))
            return None

        url = SPELL_URL_TEMPLATE.format(spell_id=spell_id)
        headers = {"Authorization": f"Bearer {token}"}
        params = {"namespace": self._config.namespace, "locale": self._config.locale}

        for attempt in range(1, self._config.max_attempts + 1):
            try:
                res = self._client.get(url, headers=headers, params=params)
            except httpx.TransportError as exc:
                log.warning(
                    "blizzard.spell_lookup_transport_error",
                    spell_id=spell_id,
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt >= self._config.max_attempts:
                    return None
                self._sleep(self._backoff_delay(attempt))
                continue

            if res.status_code == 200:
                data = res.json()
                name = data.get("name")
                if isinstance(name, dict):
                    name = name.get(self._config.locale)
                return name

            if res.status_code == 404:
                return None

            if res.status_code not in _RETRYABLE_STATUS:
                log.warning(
                    "blizzard.spell_lookup_failed", spell_id=spell_id, status=res.status_code
                )
                return None

            if attempt >= self._config.max_attempts:
                log.warning(
                    "blizzard.spell_lookup_exhausted", spell_id=spell_id, status=res.status_code
                )
                return None

            delay = self._retry_after_seconds(res) or self._backoff_delay(attempt)
            self._sleep(delay)

        return None

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
