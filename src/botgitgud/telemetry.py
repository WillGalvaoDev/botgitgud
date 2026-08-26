"""Telemetria operacional por análise — o smoke do Fiskowl funcionou, mas o
PASS operacional não pôde ser provado depois porque tudo vivia em stdout.

Fica na raiz do pacote, ao lado de `errors.py`/`logging_setup.py`, por ser
neutra de camada: `wcl/` registra as queries, `analysis/` marca os papéis e
`bot/` persiste o artefato — sem nenhuma dessas camadas importar a outra.

O recorder ativo viaja por `ContextVar`, então `WclClient.query` reporta sem que
nenhum parâmetro novo atravesse a pilha inteira. Fora de uma análise não há
recorder e a chamada é um no-op.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum


class QueryRole(StrEnum):
    """Quem motivou a query. Não se infere por `op_name`: a mesma operação
    (`fetch_player_meta`) serve tanto o jogador analisado quanto um membro da
    coorte — o que distingue é a fase do pipeline.
    """

    PLAYER_ANALYZED = "player_analyzed"
    REFERENCE = "reference"
    UNATTRIBUTED = "unattributed"


class AnalysisRecorder:
    """Acumula o que aconteceu numa análise. Thread-safe: `fetch_many` roda em
    ThreadPoolExecutor e vários workers registram ao mesmo tempo.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_op: Counter[str] = Counter()
        self._by_role: Counter[str] = Counter()
        self._retries = 0
        self.reference_members_expected: int | None = None
        self.reference_members_cache_hit: int | None = None
        self.reference_members_refetched: int | None = None

    def record_query(self, op_name: str, *, role: QueryRole, attempt: int) -> None:
        with self._lock:
            self._by_op[op_name] += 1
            self._by_role[str(role)] += 1
            if attempt > 1:
                self._retries += 1

    def record_reference_batch(self, *, expected: int, cache_hits: int, fetched: int) -> None:
        with self._lock:
            self.reference_members_expected = expected
            self.reference_members_cache_hit = cache_hits
            self.reference_members_refetched = fetched

    @property
    def queries_by_op_name(self) -> dict[str, int]:
        with self._lock:
            return dict(sorted(self._by_op.items()))

    @property
    def queries_total(self) -> int:
        with self._lock:
            return sum(self._by_op.values())

    @property
    def retries(self) -> int:
        with self._lock:
            return self._retries

    def queries_for(self, role: QueryRole) -> int:
        with self._lock:
            return self._by_role.get(str(role), 0)


_active: ContextVar[AnalysisRecorder | None] = ContextVar("analysis_recorder", default=None)
_role: ContextVar[QueryRole] = ContextVar("analysis_role", default=QueryRole.UNATTRIBUTED)


def active_recorder() -> AnalysisRecorder | None:
    return _active.get()


@contextmanager
def recording(recorder: AnalysisRecorder) -> Iterator[AnalysisRecorder]:
    token = _active.set(recorder)
    try:
        yield recorder
    finally:
        _active.reset(token)


@contextmanager
def role_scope(role: QueryRole) -> Iterator[None]:
    """Marca a fase do pipeline para atribuir as queries que ocorrerem nela."""
    token = _role.set(role)
    try:
        yield
    finally:
        _role.reset(token)


def record_query(op_name: str, *, attempt: int = 1) -> None:
    """Chamado pelo ponto central (`WclClient.query`). No-op fora de análise."""
    recorder = _active.get()
    if recorder is not None:
        recorder.record_query(op_name, role=_role.get(), attempt=attempt)


def record_reference_batch(*, expected: int, cache_hits: int, fetched: int) -> None:
    recorder = _active.get()
    if recorder is not None:
        recorder.record_reference_batch(expected=expected, cache_hits=cache_hits, fetched=fetched)
