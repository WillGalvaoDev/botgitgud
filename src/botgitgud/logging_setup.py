"""T1.1 — structlog configuration: ISO timestamp, level, logger name, and a
correlation_id that follows a request through every nested call via
structlog's contextvars mechanism (no need to thread it through every
function signature).

B5 (`docs/v1-operational-logging.md`): o console é efêmero. Um soak de 24h
precisa de uma trilha cronológica que sobreviva ao terminal e ao processo, então
`enable_file_logging` acrescenta um segundo destino — JSON Lines, rotacionado,
sob `data/logs/` — **sem** substituir o console nem as três fontes de verdade
que já existem (analysis-runs por análise, ops-snapshot para estado atual,
tabela `jobs` para a fila).

O arquivo é a história; o snapshot é o agora; o artefato de análise é o detalhe.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import traceback
import uuid
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import structlog

LOG_DIRNAME = "logs"
LOG_FILENAME = "botgitgud.jsonl"
# B6: log próprio do supervisor de processo — nunca o mesmo arquivo do `serve`,
# mesmo os dois rodando ao mesmo tempo (são processos Python separados).
SUPERVISOR_LOG_FILENAME = "supervisor.jsonl"

# Marca o handler que este módulo instala, para que uma reconfiguração troque o
# destino em vez de empilhar um segundo arquivo escrevendo as mesmas linhas.
_FILE_HANDLER_FLAG = "_botgitgud_operational_file"

# Chaves cujo VALOR nunca pode ir para o disco. A comparação é por substring no
# nome da chave: um campo novo chamado `refresh_token` ou `wcl_client_secret`
# fica coberto sem ninguém precisar lembrar de atualizar esta lista.
_SECRET_KEY_MARKERS = (
    "token",
    "secret",
    "password",
    "passwd",
    "authorization",
    "api_key",
    "apikey",
    "credential",
)
REDACTED = "***redacted***"

_session_id: str | None = None


def log_dir_for(data_dir: Path) -> Path:
    """Fora da árvore versionada, ao lado do warehouse (`data/` já é ignorado)."""
    return Path(data_dir) / LOG_DIRNAME


def current_session_id() -> str | None:
    """Id do processo em execução — é por ele que se isola um soak inteiro."""
    return _session_id


def _redact_secrets(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Nenhum segredo chega ao disco, mesmo que alguém logue um dict inteiro.

    Redigir na fronteira de escrita (e não em cada chamada) é o que torna a
    garantia verificável: não depende de todo autor futuro lembrar da regra.
    """
    for key in list(event_dict):
        lowered = key.lower()
        if any(marker in lowered for marker in _SECRET_KEY_MARKERS):
            event_dict[key] = REDACTED
    return event_dict


def _session_and_pid(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    event_dict.setdefault("pid", os.getpid())
    if _session_id is not None:
        event_dict.setdefault("session_id", _session_id)
    return event_dict


def _exception_fields(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Explode `exc_info` em campos próprios, preservando o traceback.

    Uma exceção inesperada é justamente o evento em que "a mensagem virou
    string" não basta: sem tipo e sem stack não dá para reconstruir o que
    aconteceu depois que o processo morreu. O traceback vira uma string JSON,
    então as quebras de linha são escapadas e a linha continua sendo UMA linha.
    """
    exc_info = event_dict.pop("exc_info", None)
    if not exc_info:
        return event_dict
    if exc_info is True:
        import sys

        exc_info = sys.exc_info()
    if isinstance(exc_info, BaseException):
        exc_info = (type(exc_info), exc_info, exc_info.__traceback__)
    exc_type, exc_value, exc_tb = exc_info
    if exc_type is None:
        return event_dict
    event_dict["exception_type"] = exc_type.__name__
    event_dict["exception_message"] = str(exc_value)
    event_dict["traceback"] = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    return event_dict


def configure_logging(*, level: str = "INFO", json_output: bool = False) -> None:
    """Wires structlog through Python's stdlib `logging` (structlog's own
    documented recipe for this) rather than a bare PrintLoggerFactory: the
    "nome do logger" processor (`add_logger_name`) requires a real
    `logging.Logger` with a `.name` attribute, which only the stdlib
    integration provides. This also gets handler/rotation compatibility for
    free once the pipeline needs it.
    """
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        # Redigir aqui cobre console E arquivo. O sink de arquivo redige de
        # novo por conta propria: ele nao pode depender de o console estar
        # configurado corretamente.
        _redact_secrets,
    ]

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    # Preserva o sink de arquivo se ele já estiver instalado: console e arquivo
    # coexistem, e reconfigurar o console não pode cegar o soak.
    file_handlers = [h for h in root_logger.handlers if getattr(h, _FILE_HANDLER_FLAG, False)]
    root_logger.handlers = [handler, *file_handlers]
    root_logger.setLevel(level.upper())


def _file_formatter() -> structlog.stdlib.ProcessorFormatter:
    return structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _session_and_pid,
            _exception_fields,
            _redact_secrets,
            # JSONRenderer escapa quebras de linha dentro das strings, então um
            # traceback inteiro continua cabendo em UMA linha do JSONL.
            structlog.processors.JSONRenderer(),
        ],
    )


def enable_file_logging(
    data_dir: Path,
    *,
    max_bytes: int,
    backup_count: int,
    session_id: str | None = None,
    filename: str = LOG_FILENAME,
) -> str:
    """Instala o sink durável e devolve o `session_id` deste processo.

    Rotação por TAMANHO (não diária) porque o que precisa ser limitado aqui é o
    disco, e o volume de eventos depende da carga, não do relógio: um dia
    parado gera quase nada e um dia de prewarm gera muito. `backup_count`
    dá o teto: `max_bytes * (backup_count + 1)`.

    Chamar duas vezes troca o destino em vez de duplicar linhas.

    `filename` existe para o supervisor de processo (B6): ele roda como um
    processo Python SEPARADO do bot (sem dois writers concorrentes no mesmo
    arquivo), mas mesmo assim nunca deve escrever em `botgitgud.jsonl` — esse é
    o sink do processo `serve`. `supervisor.jsonl` usa este parâmetro.
    """
    global _session_id
    _session_id = session_id or uuid.uuid4().hex[:12]

    directory = log_dir_for(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        directory / filename,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
        delay=False,
    )
    handler.setFormatter(_file_formatter())
    setattr(handler, _FILE_HANDLER_FLAG, True)

    root_logger = logging.getLogger()
    _close_file_handlers(root_logger)
    root_logger.addHandler(handler)
    return _session_id


def disable_file_logging() -> None:
    """Remove e fecha o sink — usado por testes e pelo encerramento limpo."""
    global _session_id
    _close_file_handlers(logging.getLogger())
    _session_id = None


def _close_file_handlers(root_logger: logging.Logger) -> None:
    for existing in list(root_logger.handlers):
        if getattr(existing, _FILE_HANDLER_FLAG, False):
            root_logger.removeHandler(existing)
            existing.close()


@contextmanager
def correlation_scope(correlation_id: str | None = None) -> Iterator[str]:
    """Bind `correlation_id` for the duration of the `with` block. Every log
    line emitted anywhere within it — including from deeply nested calls,
    since this rides structlog's contextvars rather than a passed-around
    parameter — carries the same id.

    Uses `bound_contextvars` (not a manual bind/unbind pair) so nested scopes
    correctly restore the outer id on exit instead of clearing it entirely.
    """
    cid = correlation_id or uuid.uuid4().hex[:12]
    with structlog.contextvars.bound_contextvars(correlation_id=cid):
        yield cid
