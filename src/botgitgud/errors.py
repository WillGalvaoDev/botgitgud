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


# CL.0-hardening: os dois únicos valores válidos de `CohortDeferredBudget.
# defer_reason` — códigos curtos e estruturados, nunca texto livre, mesma
# convenção que `bot/benchmark_job.py` já usa para `benchmark_build`
# ("benchmark_deferred_budget"/"benchmark_no_progress"). Vivem aqui (não
# como o `DeferReason` de `analysis/cohort_increment.py`, importado) porque
# `errors.py` é folha por desenho — zero import interno — e importar
# `cohort_increment` criaria um ciclo (`cohort_increment` importa de
# `analysis/cold_build.py`, que já importa `CohortDeferredBudget` daqui).
# Os dois vocabulários descrevem o mesmo par de causas por construção; quem
# constrói a exceção (analysis/pipeline.py, analysis/cold_build.py) já tem
# `DeferReason` em escopo e converte.
COLD_COHORT_BUDGET = "cold_cohort_budget"
COLD_COHORT_NO_PROGRESS = "cold_cohort_no_progress"


class CohortDeferredBudget(AnalysisError):
    """Trabalho VALIDO aguardando orcamento — nunca uma falha de analise.

    B2: por herdar de AnalysisError esta excecao caia no ramo generico do
    worker e o job era marcado `failed`, contradizendo a propria mensagem
    ("tente novamente mais tarde") e descartando o pedido do usuario. O
    tratamento correto e um estado transitorio e retomavel; quem consome esta
    excecao DEVE trata-la antes de qualquer `except BotGitGudError`.

    `planned`/`completed` carregam o progresso ja persistido no cache de logs,
    para que a telemetria prove quanto avancou antes do adiamento.

    CL.0-hardening: `defer_reason` (um de `COLD_COHORT_BUDGET`/
    `COLD_COHORT_NO_PROGRESS`, ou `None` para um chamador que ainda não foi
    atualizado) é a causa ESTRUTURAL — nunca inferida de `message`, que
    continua livre para descrever o adiamento ao usuário. `None` é o
    default seguro: um consumidor que não sabe a causa trata como se fosse
    orçamento, nunca presume NO_PROGRESS sem essa informação vir explícita.
    """

    def __init__(
        self,
        message: str,
        *,
        cohort_id: str,
        estimated_api_points: float,
        available_api_points: float,
        protected_floor: float,
        safety_margin: float,
        planned: int | None = None,
        completed: int | None = None,
        retry_after_s: float | None = None,
        defer_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.cohort_id = cohort_id
        self.estimated_api_points = estimated_api_points
        self.available_api_points = available_api_points
        self.protected_floor = protected_floor
        self.safety_margin = safety_margin
        self.planned = planned
        self.completed = completed
        self.retry_after_s = retry_after_s
        self.defer_reason = defer_reason

    @property
    def remaining(self) -> int | None:
        if self.planned is None or self.completed is None:
            return None
        return self.planned - self.completed
