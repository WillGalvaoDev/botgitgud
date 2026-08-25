"""Error hierarchy for botgitgud (§1.3 of docs/implementacao.md).

BotGitGudError
├── ConfigError
├── ApiError
│   ├── AuthError
│   ├── RateLimitBudgetExceeded
│   ├── RateLimitCheckFailed
│   ├── WclGraphQLError
│   └── TransientApiError
├── DataError
│   ├── PlayerNotFound
│   ├── FightNotFound
│   └── InsufficientCohort
└── AnalysisError
    ├── ScopeRejected
    └── CohortNotReady
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


class RateLimitCheckFailed(ApiError):
    """The rate-limit budget could not be determined after retries — the
    remote state is unknown, not known-low. Distinct from
    RateLimitBudgetExceeded (which means the budget IS known to be below
    the floor): this means the check itself failed, so a caller must fail
    closed rather than assume a budget exists.
    """


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


class ScopeRejected(AnalysisError):
    """T1.6: raised by analysis/pipeline.py's scope gate (T0.9, §1.4) for a
    tank/healer/Augmentation/unrecognized spec — str(e) is already the
    user-facing message from domain.specs.rejection_message().
    """


class CohortNotReady(AnalysisError):
    """T1.7: raised by run_analysis(..., allow_cold_build=False) when no
    CohortProfile exists yet for the request's criteria — the interactive
    (Discord) path must never build a 100-log cohort synchronously.
    """


class CohortDeferredBudget(AnalysisError):
    """A feasible cold build was deferred to preserve the WCL budget."""

    def __init__(
        self,
        message: str,
        *,
        cohort_id: str,
        estimated_api_points: float,
        available_api_points: float,
        protected_floor: float,
        safety_margin: float,
    ) -> None:
        super().__init__(message)
        self.cohort_id = cohort_id
        self.estimated_api_points = estimated_api_points
        self.available_api_points = available_api_points
        self.protected_floor = protected_floor
        self.safety_margin = safety_margin
