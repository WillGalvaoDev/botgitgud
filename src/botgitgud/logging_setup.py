"""T1.1 — structlog configuration: ISO timestamp, level, logger name, and a
correlation_id that follows a request through every nested call via
structlog's contextvars mechanism (no need to thread it through every
function signature).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import structlog


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
    root_logger.handlers = [handler]
    root_logger.setLevel(level.upper())


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
