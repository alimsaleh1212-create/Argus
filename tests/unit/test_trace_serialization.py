"""Unit tests for trace-tree serialization helpers (T040)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.domain.telemetry import Span, SpanKind, SpanStatus, TelemetryRecord, TraceTree
from backend.routers.incidents import _span_to_view, _EMPTY_TELEMETRY

_T0 = datetime(2026, 6, 12, 9, 0, 0, tzinfo=UTC)
_T1 = _T0 + timedelta(milliseconds=500)


def _make_root(*, tokens_in: int | None = 10, tokens_out: int | None = 20) -> Span:
    return Span(
        span_id="root-span",
        trace_id="trace-001",
        correlation_id="corr-001",
        name="incident_pipeline",
        kind=SpanKind.ROOT,
        started_at=_T0,
        ended_at=_T1,
        status=SpanStatus.OK,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )


def _make_child(
    *,
    span_id: str = "child-span",
    parent_span_id: str = "root-span",
    kind: SpanKind = SpanKind.AGENT_STEP,
    status: SpanStatus = SpanStatus.OK,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    llm_model: str | None = None,
    error_message: str | None = None,
) -> Span:
    return Span(
        span_id=span_id,
        trace_id="trace-001",
        correlation_id="corr-001",
        name="triage",
        kind=kind,
        started_at=_T0,
        ended_at=_T1,
        status=status,
        parent_span_id=parent_span_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        llm_model=llm_model,
        error_message=error_message,
    )


class TestSpanToView:
    def test_basic_field_mapping(self) -> None:
        span = _make_root()
        view = _span_to_view(span)
        assert view.span_id == "root-span"
        assert view.parent_span_id is None
        assert view.name == "incident_pipeline"
        assert view.kind == "root"
        assert view.status == "ok"

    def test_kind_and_status_are_strings(self) -> None:
        span = _make_child(kind=SpanKind.LLM_CALL, status=SpanStatus.ERROR)
        view = _span_to_view(span)
        assert isinstance(view.kind, str)
        assert isinstance(view.status, str)
        assert view.kind == "llm_call"
        assert view.status == "error"

    def test_tokens_preserved_when_set(self) -> None:
        span = _make_root(tokens_in=50, tokens_out=120)
        view = _span_to_view(span)
        assert view.tokens_in == 50
        assert view.tokens_out == 120

    def test_null_tokens_preserved_as_null_not_zero(self) -> None:
        span = _make_root(tokens_in=None, tokens_out=None)
        view = _span_to_view(span)
        assert view.tokens_in is None
        assert view.tokens_out is None

    def test_latency_ms_computed_from_timestamps(self) -> None:
        span = _make_root()
        view = _span_to_view(span)
        assert view.latency_ms == 500

    def test_latency_ms_none_when_no_ended_at(self) -> None:
        span = Span(
            span_id="s",
            trace_id="t",
            correlation_id="c",
            name="n",
            kind=SpanKind.AGENT_STEP,
            started_at=_T0,
            ended_at=None,
        )
        view = _span_to_view(span)
        assert view.latency_ms is None

    def test_parent_span_id_mapped(self) -> None:
        span = _make_child(parent_span_id="parent-99")
        view = _span_to_view(span)
        assert view.parent_span_id == "parent-99"

    def test_llm_model_and_error_message_mapped(self) -> None:
        span = _make_child(
            llm_model="gemini-1.5-pro",
            error_message="context length exceeded",
        )
        view = _span_to_view(span)
        assert view.llm_model == "gemini-1.5-pro"
        assert view.error_message == "context length exceeded"

    def test_attributes_default_empty_dict(self) -> None:
        span = _make_root()
        view = _span_to_view(span)
        assert view.attributes == {}

    def test_attributes_passed_through(self) -> None:
        span = _make_root()
        span.attributes = {"key": "value", "num": 42}
        view = _span_to_view(span)
        assert view.attributes == {"key": "value", "num": 42}


class TestEmptyTelemetry:
    def test_defaults_to_zero_counts_null_metrics(self) -> None:
        assert _EMPTY_TELEMETRY.step_count == 0
        assert _EMPTY_TELEMETRY.error_steps == 0
        assert _EMPTY_TELEMETRY.total_tokens_in is None
        assert _EMPTY_TELEMETRY.total_tokens_out is None
        assert _EMPTY_TELEMETRY.end_to_end_ms is None


class TestTelemetryFromTraceTree:
    def _make_tree(self, children: dict[str, list[Span]] | None = None) -> TraceTree:
        root = _make_root(tokens_in=10, tokens_out=20)
        return TraceTree(root=root, children=children or {})

    def test_root_only_tree_step_count_zero(self) -> None:
        tree = self._make_tree()
        rec = TelemetryRecord.from_trace_tree(tree)
        assert rec.step_count == 0

    def test_children_counted_in_step_count(self) -> None:
        child1 = _make_child(span_id="c1", kind=SpanKind.AGENT_STEP)
        child2 = _make_child(span_id="c2", kind=SpanKind.LLM_CALL)
        tree = self._make_tree({"root-span": [child1, child2]})
        rec = TelemetryRecord.from_trace_tree(tree)
        assert rec.step_count == 2

    def test_error_steps_counted(self) -> None:
        ok = _make_child(span_id="c1", status=SpanStatus.OK)
        err = _make_child(span_id="c2", status=SpanStatus.ERROR)
        tree = self._make_tree({"root-span": [ok, err]})
        rec = TelemetryRecord.from_trace_tree(tree)
        assert rec.error_steps == 1

    def test_end_to_end_ms_from_root_latency(self) -> None:
        tree = self._make_tree()
        rec = TelemetryRecord.from_trace_tree(tree)
        assert rec.end_to_end_ms == 500

    def test_total_tokens_aggregated_from_llm_spans_only(self) -> None:
        # Tokens are summed only from LLM_CALL spans; root (ROOT kind) is excluded.
        llm1 = _make_child(span_id="c1", tokens_in=5, tokens_out=15, kind=SpanKind.LLM_CALL)
        llm2 = _make_child(span_id="c2", tokens_in=3, tokens_out=10, kind=SpanKind.LLM_CALL)
        tree = self._make_tree({"root-span": [llm1, llm2]})
        rec = TelemetryRecord.from_trace_tree(tree)
        assert rec.total_tokens_in == 8
        assert rec.total_tokens_out == 25

    def test_null_tokens_not_coerced_to_zero(self) -> None:
        root = _make_root(tokens_in=None, tokens_out=None)
        tree = TraceTree(root=root, children={})
        rec = TelemetryRecord.from_trace_tree(tree)
        assert rec.total_tokens_in is None
        assert rec.total_tokens_out is None
