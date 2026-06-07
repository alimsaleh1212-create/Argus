"""Structured, correlated, redacted logging seam (SPEC-observability #2).

Builds on the #1 structlog chain and adds:
  1. A redaction processor (before JSONRenderer) so no logging path bypasses redaction.
  2. bind_incident() / clear_incident() helpers that set correlation_id + trace_id
     in structlog.contextvars for the duration of an incident.
  3. No-incident safety: lines emitted without a bound incident carry
     correlation_id="-" and no_incident=True (FR-011).

Processor chain order:
  merge_contextvars → add_log_level → TimeStamper → StackInfoRenderer
  → _redact_event_dict  (NEW — secret scrubber + PII at LOG boundary, fail-closed)
  → JSONRenderer
"""

from __future__ import annotations

import io
import logging
import sys
from typing import Any

import structlog


def _redact_event_dict(
    logger: Any,
    method: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """structlog processor: redact every string value at the LOG boundary.

    Fail-closed per-field: a redaction error on one field drops that field
    (replaced with [REDACTION-FAILED]) rather than dropping the whole line or
    emitting the raw value. The line is always emitted.

    Uses the module reference for _redact_str so tests can monkeypatch it.
    """
    import backend.infra.redaction as _redact_mod  # noqa: PLC0415
    from backend.domain.redaction import Boundary

    _fail_marker = "[REDACTION-FAILED]"

    def _scrub(text: str) -> str:
        try:
            return _redact_mod._redact_str(text, Boundary.LOG)
        except Exception:
            return _fail_marker

    result: dict[str, Any] = {}
    for key, value in event_dict.items():
        try:
            if isinstance(value, str):
                result[key] = _scrub(value)
            elif isinstance(value, dict):
                inner: dict[str, Any] = {}
                for k, v in value.items():
                    inner[k] = _scrub(v) if isinstance(v, str) else v
                result[key] = inner
            else:
                result[key] = value
        except Exception:
            result[key] = _fail_marker

    return result


def _ensure_correlation_id(
    logger: Any,
    method: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """structlog processor: inject correlation_id="-" when no incident is bound."""
    if "correlation_id" not in event_dict:
        event_dict.setdefault("correlation_id", "-")
        event_dict.setdefault("no_incident", True)
    return event_dict


def configure_logging(
    log_level: str = "INFO",
    output: io.IOBase | None = None,
) -> None:
    """Configure structlog with JSON rendering, redaction, and correlation-id binding."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _ensure_correlation_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            _redact_event_dict,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(log_level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(
            file=output if output is not None else sys.stdout
        ),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def bind_incident(correlation_id: str, trace_id: str | None = None) -> None:
    """Bind the incident correlation id into structlog contextvars.

    Every subsequent log line on this async task/thread will carry the id.
    Call clear_incident() (or use as context manager) when the incident ends.
    """
    structlog.contextvars.bind_contextvars(
        correlation_id=correlation_id,
        trace_id=trace_id if trace_id is not None else correlation_id,
    )


def clear_incident() -> None:
    """Remove incident bindings from structlog contextvars."""
    structlog.contextvars.unbind_contextvars("correlation_id", "trace_id")
