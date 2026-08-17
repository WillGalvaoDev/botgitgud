"""Error hierarchy for botgitgud (§1.3 of docs/implementacao.md).

BotGitGudError
├── ConfigError
├── ApiError
│   ├── AuthError
│   ├── RateLimitBudgetExceeded
│   ├── WclGraphQLError
│   └── TransientApiError
├── DataError
│   ├── PlayerNotFound
│   ├── FightNotFound
│   └── InsufficientCohort
└── AnalysisError
"""

from __future__ import annotations


class BotGitGudError(Exception):
    """Root of every domain error raised by this project."""


class ConfigError(BotGitGudError):
    """Invalid or missing configuration (credentials, settings, ...)."""


class ApiError(BotGitGudError):
    """Base for failures talking to an external API (WCL, Blizzard, Discord)."""


class AuthError(ApiError):
    """Authentication/authorization failure (401/403). Never retried."""


class RateLimitBudgetExceeded(ApiError):
    """The configured API-points floor was breached before making a call."""

    def __init__(self, message: str, *, points_remaining: float, reset_in_seconds: float) -> None:
        super().__init__(message)
        self.points_remaining = points_remaining
        self.reset_in_seconds = reset_in_seconds


class WclGraphQLError(ApiError):
    """A 200 OK response whose `errors` field is non-empty."""

    def __init__(self, message: str, *, errors: list[dict[str, object]]) -> None:
        super().__init__(message)
        self.errors = errors


class TransientApiError(ApiError):
    """A retryable failure (429/5xx/network) that exhausted every attempt."""


class DataError(BotGitGudError):
    """The API answered successfully but the data doesn't fit what was asked."""


class PlayerNotFound(DataError):
    pass


class FightNotFound(DataError):
    pass


class InsufficientCohort(DataError):
    def __init__(self, message: str, *, n_members: int, minimum_required: int) -> None:
        super().__init__(message)
        self.n_members = n_members
        self.minimum_required = minimum_required


class AnalysisError(BotGitGudError):
    """Failure in the analysis pipeline itself (alignment, profiling, ...)."""
