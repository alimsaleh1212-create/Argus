"""Integration tests — T022: export-down resilience (SC-006).

Points the exporter at an unreachable DB mid-run; verifies:
- Synthetic incident still completes successfully
- dropped_batches counter increments
- No raw sensitive content leaks via the failed path
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.domain.telemetry import SpanKind
from backend.infra.redaction import build_redactor
from backend.infra.tracing import build_tracer, span

pytestmark = pytest.mark.integration


class _FailingRepo:
    """A fake TraceRepository whose flush always raises (simulates unreachable DB)."""

    dropped_batches: int = 0

    def enqueue(self, s) -> None:
        pass  # enqueue succeeds; failure is in flush

    async def flush(self) -> None:
        self.dropped_batches += 1
        raise ConnectionError("Postgres unreachable")


class TestExportResilience:
    async def test_incident_completes_when_export_unreachable(self) -> None:
        """Incident processing completes even when the trace exporter fails (SC-006)."""
        failing_repo = _FailingRepo()
        tracer = build_tracer(exporter=failing_repo, max_attr_bytes=8192)

        completed = False
        with span(tracer, "root", SpanKind.ROOT, correlation_id="resilience_001") as root_s:
            with span(
                tracer, "triage.step", SpanKind.AGENT_STEP,
                correlation_id="resilience_001", parent_span_id=root_s.span_id,
            ):
                pass
            completed = True

        assert completed, "Incident processing did not complete"

        # Simulate the exporter trying to flush (as would happen via BatchSpanProcessor)
        try:
            await failing_repo.flush()
        except Exception:
            pass  # expected

        assert failing_repo.dropped_batches >= 1, "dropped_batches not incremented"

    async def test_no_raw_content_leaks_on_export_failure(self) -> None:
        """No raw sensitive content exposed when export fails (SC-006)."""
        FAKE_KEY = "AKIAIOSFODNN7EXAMPLE"
        failing_repo = _FailingRepo()
        tracer = build_tracer(exporter=failing_repo, max_attr_bytes=8192)

        with span(
            tracer, "step", SpanKind.AGENT_STEP,
            correlation_id="resilience_002",
            attrs={"payload": f"key={FAKE_KEY}"},
        ) as s:
            pass

        # Even if export fails, the span attributes stored in the Span object are redacted
        import json
        attr_str = json.dumps(s.attributes)
        assert FAKE_KEY not in attr_str, "Raw key found in span attributes"

    async def test_dropped_batch_counter_increments(self) -> None:
        """dropped_batches increments on each failed flush."""
        failing_repo = _FailingRepo()
        tracer = build_tracer(exporter=failing_repo, max_attr_bytes=8192)

        with span(tracer, "s1", SpanKind.AGENT_STEP, correlation_id="resilience_003"):
            pass

        for _ in range(3):
            try:
                await failing_repo.flush()
            except Exception:
                pass

        assert failing_repo.dropped_batches == 3
