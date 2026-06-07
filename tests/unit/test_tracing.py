"""Unit tests — T015: OTel tracer, span() helper, LLM usage recording.

LLM calls are mocked; no Postgres or testcontainer needed.
Covers:
- record_llm_usage sets tokens_in/out, model, latency on the span
- Missing provider usage → tokens remain None (rendered as "unknown", SC-004)
- Oversized attribute truncated AFTER redaction (no raw substring re-exposed, FR-017)
- Span attributes redacted at TRACE boundary before storage
- Span nesting: child spans have parent_span_id set correctly
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from backend.domain.telemetry import Span, SpanKind, SpanStatus, TelemetryRecord, TraceTree
from backend.infra.tracing import build_tracer, record_llm_usage, span


@pytest.fixture
def tracer():
    """In-memory tracer with no Postgres exporter."""
    return build_tracer(exporter=None, max_attr_bytes=64)


class TestSpanHelper:
    def test_span_creates_and_closes(self, tracer) -> None:
        with span(tracer, "test.step", SpanKind.AGENT_STEP, correlation_id="inc_1") as s:
            assert s.span_id is not None
            assert s.status == SpanStatus.UNSET
        assert s.ended_at is not None
        assert s.status in (SpanStatus.OK, SpanStatus.UNSET)

    def test_child_span_has_parent_id(self, tracer) -> None:
        with span(tracer, "root", SpanKind.ROOT, correlation_id="inc_2") as root:
            with span(
                tracer, "child", SpanKind.TOOL_CALL, correlation_id="inc_2",
                parent_span_id=root.span_id
            ) as child:
                assert child.parent_span_id == root.span_id

    def test_exception_sets_error_status(self, tracer) -> None:
        with pytest.raises(ValueError):
            with span(tracer, "failing", SpanKind.AGENT_STEP, correlation_id="inc_3") as s:
                raise ValueError("step failed")
        assert s.status == SpanStatus.ERROR

    def test_attributes_set_on_span(self, tracer) -> None:
        with span(
            tracer, "step", SpanKind.AGENT_STEP, correlation_id="inc_4",
            attrs={"input": "some data", "safe_key": "safe_value"}
        ) as s:
            pass
        assert "safe_key" in s.attributes or "input" in s.attributes


class TestRecordLlmUsage:
    def test_tokens_and_model_recorded(self, tracer) -> None:
        usage = MagicMock()
        usage.prompt_tokens = 100
        usage.completion_tokens = 50

        with span(tracer, "llm.call", SpanKind.LLM_CALL, correlation_id="inc_5") as s:
            record_llm_usage(s, usage=usage, model="gpt-4o")

        assert s.tokens_in == 100
        assert s.tokens_out == 50
        assert s.llm_model == "gpt-4o"

    def test_missing_usage_stays_none(self, tracer) -> None:
        """Provider without usage returns None — marked 'unknown' in views (SC-004)."""
        with span(tracer, "llm.call", SpanKind.LLM_CALL, correlation_id="inc_6") as s:
            record_llm_usage(s, usage=None, model="claude-sonnet-4-6")

        assert s.tokens_in is None
        assert s.tokens_out is None
        assert s.llm_model == "claude-sonnet-4-6"

    def test_latency_derived_from_span_times(self, tracer) -> None:
        with span(tracer, "llm.call", SpanKind.LLM_CALL, correlation_id="inc_7") as s:
            record_llm_usage(s, usage=None, model="claude-sonnet-4-6")
        assert s.latency_ms is not None
        assert s.latency_ms >= 0


class TestAttributeTruncation:
    def test_oversized_attr_truncated(self, tracer) -> None:
        """Attribute exceeding max_attr_bytes is truncated (FR-017)."""
        big_value = "x" * 200  # way over 64-byte limit
        with span(
            tracer, "step", SpanKind.AGENT_STEP, correlation_id="inc_8",
            attrs={"big": big_value}
        ) as s:
            pass
        stored = s.attributes.get("big", "")
        # 64 bytes of content + up to ~15 chars for the truncation marker
        assert len(stored.encode("utf-8")) <= 64 + 20
        assert "[truncated]" in stored

    def test_truncation_after_redaction_no_raw_substr(self, tracer) -> None:
        """Truncation runs after redaction — no raw sensitive substring re-exposed (FR-017).

        The AWS key is placed at a word boundary (space after) so the pattern matches.
        """
        aws_key = "AKIAIOSFODNN7EXAMPLE"
        # Space after key gives the \b word boundary the regex needs
        padded = aws_key + " " + ("z" * 200)
        with span(
            tracer, "step", SpanKind.AGENT_STEP, correlation_id="inc_9",
            attrs={"payload": padded}
        ) as s:
            pass
        stored = str(s.attributes.get("payload", ""))
        assert aws_key not in stored


class TestTraceTree:
    def test_telemetry_record_from_tree(self, tracer) -> None:
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        root_span = Span(
            span_id="r1", trace_id="t1", correlation_id="inc_10",
            name="root", kind=SpanKind.ROOT,
            started_at=now, ended_at=now + __import__("datetime").timedelta(seconds=1),
            status=SpanStatus.OK,
        )
        llm_span = Span(
            span_id="l1", trace_id="t1", correlation_id="inc_10",
            name="llm.call", kind=SpanKind.LLM_CALL,
            started_at=now, ended_at=now + __import__("datetime").timedelta(milliseconds=200),
            parent_span_id="r1", status=SpanStatus.OK,
            tokens_in=50, tokens_out=25, llm_model="test-model",
        )
        tree = TraceTree(root=root_span, children={"r1": [llm_span]})
        rec = TelemetryRecord.from_trace_tree(tree)
        assert rec.total_tokens_in == 50
        assert rec.total_tokens_out == 25
        assert rec.step_count == 1
        assert rec.error_steps == 0
