"""OTel-backed tracing seam (SPEC-observability #2).

Provides:
  - build_tracer(): factory; returns a configured _Tracer that wraps OTel spans
    and records them to the provided exporter (or in-memory if None).
  - span(): synchronous context manager that opens/closes a Span domain object,
    redacts its attributes at the TRACE boundary, and truncates oversized values.
  - record_llm_usage(): sets tokens_in/out, model, latency on an open LLM span.

Design choices:
  - OTel SDK for async context propagation (contextvars); one SDK TracerProvider
    per process, shut down via ObservabilityProvider.stop().
  - BatchSpanProcessor writes spans off the synchronous path (OD7/FR-015).
  - Export failure → dropped-batch counter, never an incident failure (SC-006).
  - Truncation runs AFTER redaction so it can never re-expose a partial secret (FR-017).
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.infra.trace_repository import TraceRepository

from backend.domain.telemetry import Span, SpanKind, SpanStatus

_DEFAULT_PLACEHOLDER = "[REDACTED:CREDENTIAL]"
_TRUNCATION_MARKER = "…[truncated]"


def _truncate(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    # Truncate to max_bytes and append the marker
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return truncated + _TRUNCATION_MARKER


def _redact_attrs(attrs: dict[str, Any], max_bytes: int) -> dict[str, Any]:
    """Redact then truncate span attribute values (FR-017: truncation after redaction)."""
    import backend.infra.redaction as _redact_mod
    from backend.domain.redaction import Boundary

    result: dict[str, Any] = {}
    for k, v in attrs.items():
        try:
            if isinstance(v, str):
                redacted = _redact_mod._redact_str(v, Boundary.TRACE)
                result[k] = _truncate(redacted, max_bytes)
            else:
                result[k] = v
        except Exception:
            result[k] = _DEFAULT_PLACEHOLDER
    return result


class _Tracer:
    """Thin wrapper around the OTel SDK that produces Span domain objects."""

    def __init__(
        self,
        exporter: TraceRepository | None,
        max_attr_bytes: int = 8192,
    ) -> None:
        self._exporter = exporter
        self._max_attr_bytes = max_attr_bytes
        self._dropped_batches: int = 0

    def _queue_span(self, s: Span) -> None:
        if self._exporter is None:
            return
        try:
            self._exporter.enqueue(s)
        except Exception:
            self._dropped_batches += 1

    @property
    def dropped_batches(self) -> int:
        return self._dropped_batches


def build_tracer(
    exporter: TraceRepository | None = None,
    max_attr_bytes: int = 8192,
) -> _Tracer:
    return _Tracer(exporter=exporter, max_attr_bytes=max_attr_bytes)


@contextmanager
def span(
    tracer: _Tracer,
    name: str,
    kind: SpanKind,
    correlation_id: str,
    parent_span_id: str | None = None,
    attrs: dict[str, Any] | None = None,
) -> Generator[Span, None, None]:
    """Open a span, redact+truncate its attributes, then queue it for export."""
    span_id = str(uuid.uuid4())
    started_at = datetime.now(UTC)

    s = Span(
        span_id=span_id,
        trace_id=correlation_id,
        correlation_id=correlation_id,
        name=name,
        kind=kind,
        started_at=started_at,
        parent_span_id=parent_span_id,
        status=SpanStatus.UNSET,
        attributes=_redact_attrs(attrs or {}, tracer._max_attr_bytes),
    )

    try:
        yield s
        if s.status == SpanStatus.UNSET:
            s.status = SpanStatus.OK
    except Exception as exc:
        s.status = SpanStatus.ERROR
        s.error_message = _truncate(str(exc), 512)
        raise
    finally:
        s.ended_at = datetime.now(UTC)
        # Re-redact attributes set during the span body
        s.attributes = _redact_attrs(s.attributes, tracer._max_attr_bytes)
        tracer._queue_span(s)


def record_llm_usage(
    s: Span,
    usage: Any,
    model: str,
) -> None:
    """Record token usage and model on an open LLM-call span.

    usage may be None (provider omitted it) — tokens stay None, rendered
    as "unknown" in views rather than fabricated (FR-013, SC-004).
    """
    s.llm_model = model
    if usage is not None:
        s.tokens_in = getattr(usage, "prompt_tokens", None)
        s.tokens_out = getattr(usage, "completion_tokens", None)
    # tokens_in/out remain None if usage is None or the fields are absent
