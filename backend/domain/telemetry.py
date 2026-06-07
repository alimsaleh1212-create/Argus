"""Telemetry domain types — pure, no outward dependencies (SPEC-observability #2).

These are the shapes the trace store, dashboard (#12), and eval (#13) consume.
Nothing here is incident/business logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class SpanStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    UNSET = "unset"


class SpanKind(StrEnum):
    ROOT = "root"
    AGENT_STEP = "agent_step"
    TOOL_CALL = "tool_call"
    RETRIEVAL = "retrieval"
    LLM_CALL = "llm_call"


@dataclass
class Span:
    """One recorded unit of work within an incident.

    ``attributes`` must be pre-redacted (TRACE boundary) before storage.
    ``tokens_in``/``tokens_out`` are None when the provider omits usage (FR-013);
    rendered as "unknown" in views.
    """

    span_id: str
    trace_id: str
    correlation_id: str
    name: str
    kind: SpanKind
    started_at: datetime
    parent_span_id: str | None = None
    status: SpanStatus = SpanStatus.UNSET
    ended_at: datetime | None = None
    llm_model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None

    @property
    def latency_ms(self) -> int | None:
        if self.started_at and self.ended_at:
            delta = self.ended_at - self.started_at
            return int(delta.total_seconds() * 1000)
        return None


@dataclass
class TraceTree:
    """A complete trace tree for one incident — built from trace_spans on read.

    Invariants (SC-003):
      - Exactly one root span (parent_span_id is None).
      - Every non-root span's parent_span_id resolves within the same trace_id.
      - No orphaned or duplicated spans.
    """

    root: Span
    children: dict[str, list[Span]] = field(default_factory=dict)

    def all_spans(self) -> list[Span]:
        """Return all spans in BFS order."""
        result = [self.root]
        queue = [self.root.span_id]
        while queue:
            parent_id = queue.pop(0)
            for child in self.children.get(parent_id, []):
                result.append(child)
                queue.append(child.span_id)
        return result


@dataclass
class TelemetryRecord:
    """Per-incident aggregate KPIs derived from spans (FR-016).

    Consumed by the dashboard trace inspector and eval gates (#12/#13).
    tokens_* sum only known (non-None) values; None means "unknown".
    """

    correlation_id: str
    total_tokens_in: int | None
    total_tokens_out: int | None
    end_to_end_ms: int | None
    step_count: int
    error_steps: int

    @classmethod
    def from_trace_tree(cls, tree: TraceTree) -> TelemetryRecord:
        spans = tree.all_spans()
        llm_spans = [s for s in spans if s.kind == SpanKind.LLM_CALL]
        tin = (
            sum(s.tokens_in for s in llm_spans if s.tokens_in is not None) or None
            if any(s.tokens_in is not None for s in llm_spans)
            else None
        )
        tout = (
            sum(s.tokens_out for s in llm_spans if s.tokens_out is not None) or None
            if any(s.tokens_out is not None for s in llm_spans)
            else None
        )
        return cls(
            correlation_id=tree.root.correlation_id,
            total_tokens_in=tin,
            total_tokens_out=tout,
            end_to_end_ms=tree.root.latency_ms,
            step_count=len([s for s in spans if s.kind != SpanKind.ROOT]),
            error_steps=len([s for s in spans if s.status == SpanStatus.ERROR]),
        )


@dataclass
class LogContext:
    """Fields bound into structlog.contextvars for every line in an incident."""

    correlation_id: str
    trace_id: str
    component: str = ""

    @property
    def no_incident(self) -> bool:
        return self.correlation_id == "-"
