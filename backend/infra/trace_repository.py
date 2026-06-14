"""Trace repository — persist and query spans from the trace_spans Postgres table.

Spans are enqueued synchronously (in the span() context manager) and flushed
asynchronously off the incident path (FR-015). Flush failure increments a
dropped-batch counter; it never fails an incident (SC-006).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.domain.telemetry import Span, SpanKind, SpanStatus, TraceTree


class TraceRepository:
    """Batch-persist spans and assemble TraceTree on read."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._queue: list[Span] = []
        self._dropped_batches: int = 0

    def enqueue(self, s: Span) -> None:
        """Queue a closed span for the next flush (called synchronously from span())."""
        self._queue.append(s)

    async def flush(self) -> None:
        """Persist all queued spans. On error: drop the batch and increment counter."""
        if not self._queue:
            return
        batch, self._queue = self._queue, []
        try:
            async with self._engine.begin() as conn:
                for s in batch:
                    await conn.execute(
                        text(
                            """
                            INSERT INTO trace_spans (
                                span_id, trace_id, parent_span_id, correlation_id,
                                name, kind, status, started_at, ended_at, latency_ms,
                                llm_model, tokens_in, tokens_out, attributes, error_message
                            ) VALUES (
                                :span_id, :trace_id, :parent_span_id, :correlation_id,
                                :name, :kind, :status, :started_at, :ended_at, :latency_ms,
                                :llm_model, :tokens_in, :tokens_out, CAST(:attributes AS jsonb),
                                :error_message
                            )
                            ON CONFLICT (span_id) DO NOTHING
                            """
                        ),
                        {
                            "span_id": s.span_id,
                            "trace_id": s.trace_id,
                            "parent_span_id": s.parent_span_id,
                            "correlation_id": s.correlation_id,
                            "name": s.name,
                            "kind": s.kind.value,
                            "status": s.status.value,
                            "started_at": s.started_at,
                            "ended_at": s.ended_at,
                            "latency_ms": s.latency_ms,
                            "llm_model": s.llm_model,
                            "tokens_in": s.tokens_in,
                            "tokens_out": s.tokens_out,
                            "attributes": __import__("json").dumps(s.attributes),
                            "error_message": s.error_message,
                        },
                    )
        except Exception:
            self._dropped_batches += 1

    async def get_trace_tree(self, correlation_id: str) -> TraceTree | None:
        """Load all spans for an incident and assemble a TraceTree (SC-003)."""
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text("SELECT * FROM trace_spans WHERE correlation_id = :cid ORDER BY started_at"),
                {"cid": correlation_id},
            )
            rows = result.mappings().all()

        if not rows:
            return None

        spans = [_row_to_span(r) for r in rows]
        roots = [s for s in spans if s.parent_span_id is None]
        if not roots:
            return None

        root = roots[0]
        children: dict[str, list[Span]] = defaultdict(list)
        for s in spans:
            if s.parent_span_id is not None:
                children[s.parent_span_id].append(s)

        return TraceTree(root=root, children=dict(children))

    @property
    def dropped_batches(self) -> int:
        return self._dropped_batches


def _row_to_span(row: Any) -> Span:
    import json

    attrs = row["attributes"]
    if isinstance(attrs, str):
        attrs = json.loads(attrs)
    elif attrs is None:
        attrs = {}

    return Span(
        span_id=row["span_id"],
        trace_id=row["trace_id"],
        parent_span_id=row["parent_span_id"],
        correlation_id=row["correlation_id"],
        name=row["name"],
        kind=SpanKind(row["kind"]),
        status=SpanStatus(row["status"]),
        started_at=row["started_at"].replace(tzinfo=UTC)
        if row["started_at"].tzinfo is None
        else row["started_at"],
        ended_at=row["ended_at"].replace(tzinfo=UTC)
        if row["ended_at"] and row["ended_at"].tzinfo is None
        else row["ended_at"],
        llm_model=row["llm_model"],
        tokens_in=row["tokens_in"],
        tokens_out=row["tokens_out"],
        attributes=attrs,
        error_message=row["error_message"],
    )
