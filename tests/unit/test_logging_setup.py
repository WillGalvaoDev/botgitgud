from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import structlog

from botgitgud.logging_setup import configure_logging, correlation_scope


def _capture_events() -> tuple[list[dict[str, Any]], None]:
    events: list[dict[str, Any]] = []

    def _capture(_logger: object, _method_name: str, event_dict: Any) -> Any:
        events.append(dict(event_dict))
        return event_dict

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            _capture,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        logger_factory=structlog.ReturnLoggerFactory(),
        cache_logger_on_first_use=False,
    )
    return events, None


def _nested_emit() -> None:
    """Simulates a log line from deep inside some other module — proves the
    correlation_id doesn't need to be threaded through function signatures.
    """
    structlog.get_logger("some.other.module").info("nested_event")


def test_every_log_line_within_scope_carries_same_correlation_id() -> None:
    events, _ = _capture_events()
    try:
        with correlation_scope() as cid:
            structlog.get_logger(__name__).info("first_event")
            _nested_emit()
            structlog.get_logger(__name__).info("third_event")
    finally:
        structlog.reset_defaults()

    assert len(events) == 3
    assert all(e.get("correlation_id") == cid for e in events)


def test_explicit_correlation_id_is_used_verbatim() -> None:
    events, _ = _capture_events()
    try:
        with correlation_scope("my-custom-id") as cid:
            assert cid == "my-custom-id"
            structlog.get_logger(__name__).info("event")
    finally:
        structlog.reset_defaults()

    assert events[0]["correlation_id"] == "my-custom-id"


def test_correlation_id_does_not_leak_outside_scope() -> None:
    events, _ = _capture_events()
    try:
        with correlation_scope("inside-id"):
            pass
        structlog.get_logger(__name__).info("outside_event")
    finally:
        structlog.reset_defaults()

    assert "correlation_id" not in events[0]


def test_nested_scopes_get_different_ids_and_restore_outer_on_exit() -> None:
    events, _ = _capture_events()
    try:
        with correlation_scope("outer") as outer_id:
            structlog.get_logger(__name__).info("outer_before")
            with correlation_scope("inner") as inner_id:
                structlog.get_logger(__name__).info("inner_event")
            structlog.get_logger(__name__).info("outer_after")
    finally:
        structlog.reset_defaults()

    assert outer_id == "outer"
    assert inner_id == "inner"
    assert events[0]["correlation_id"] == "outer"
    assert events[1]["correlation_id"] == "inner"
    assert events[2]["correlation_id"] == "outer"


def test_auto_generated_ids_are_unique() -> None:
    with correlation_scope() as a, correlation_scope() as b:
        pass
    assert a != b


def test_configure_logging_console_mode_does_not_raise() -> None:
    try:
        configure_logging(level="DEBUG", json_output=False)
        structlog.get_logger(__name__).info("smoke_test", key="value")
    finally:
        structlog.reset_defaults()


def test_configure_logging_json_mode_does_not_raise() -> None:
    try:
        configure_logging(level="INFO", json_output=True)
        structlog.get_logger(__name__).info("smoke_test", key="value")
    finally:
        structlog.reset_defaults()


_RAW_PRINT_CALL = re.compile(r"(?<![A-Za-z_])print\(")


def test_no_raw_print_calls_anywhere_in_src() -> None:
    """`grep -rn "print(" src/` deve retornar vazio (achado 4.9).

    Fronteira de palavra, não substring nua: EB.3 introduziu
    `population_fingerprint`/`policy_fingerprint`
    (analysis/benchmark_store_models.py), cujo nome contém "print(" só
    porque "fingerprint" tem "print" como substring — não é uma chamada à
    função embutida `print()`. `(?<![A-Za-z_])` garante que o "print(" real
    (nunca precedido por letra/underscore) continua sendo pego.
    """
    src_root = Path(__file__).resolve().parents[2] / "src"
    offenders = [
        p for p in src_root.rglob("*.py") if _RAW_PRINT_CALL.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_the_print_call_guard_still_catches_a_real_print(tmp_path: Path) -> None:
    """Guarda a própria correção acima: garante que a fronteira de palavra
    não abriu um buraco silencioso para uma chamada `print(...)` real.
    """
    decoy = tmp_path / "decoy.py"
    decoy.write_text("def f():\n    print('leaked to stdout')\n", encoding="utf-8")
    assert _RAW_PRINT_CALL.search(decoy.read_text(encoding="utf-8")) is not None

    clean = tmp_path / "clean.py"
    clean.write_text("def population_fingerprint(x):\n    return x\n", encoding="utf-8")
    assert _RAW_PRINT_CALL.search(clean.read_text(encoding="utf-8")) is None
