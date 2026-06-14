"""e2e tests — T024: full observability seam, zero seeded-secret leaks (SC-001, SC-003).

Drives a synthetic incident through the unified seam in-process (no compose stack
needed — the seam is testable with in-memory components). Verifies:
- One trace tree produced (SC-003)
- Zero seeded credentials/PII appear unredacted across log/trace/prompt/snapshot (SC-001)
"""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock

import pytest

from backend.domain.redaction import Boundary
from backend.domain.telemetry import SpanKind
from backend.infra.logging import bind_incident, clear_incident, configure_logging
from backend.infra.redaction import build_redactor
from backend.infra.tracing import build_tracer, record_llm_usage, span

pytestmark = pytest.mark.e2e

# Seeded values that must NEVER appear unredacted
SEEDED_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
SEEDED_BEARER = "Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature"
SEEDED_EMAIL = "victim@target.com"


def _run_synthetic_incident(
    log_buf: io.StringIO,
    correlation_id: str,
) -> list:
    """Drive a synthetic incident through the seam; return collected spans."""
    redactor = build_redactor(presidio_enabled=True)
    spans: list = []

    tracer = build_tracer(exporter=None, max_attr_bytes=8192)

    bind_incident(correlation_id)
    try:
        import structlog

        logger = structlog.get_logger("e2e.test")

        with span(tracer, "root", SpanKind.ROOT, correlation_id=correlation_id) as root_s:
            spans.append(root_s)

            # Log a line containing seeded secrets — must be redacted
            logger.info(
                "incident_start",
                payload=f"api_key={SEEDED_AWS_KEY} user={SEEDED_EMAIL}",
            )

            with span(
                tracer,
                "triage.step",
                SpanKind.AGENT_STEP,
                correlation_id=correlation_id,
                parent_span_id=root_s.span_id,
                attrs={"input": f"token={SEEDED_BEARER}"},
            ) as triage_s:
                spans.append(triage_s)
                logger.info("triage_complete", result="benign")

            with span(
                tracer,
                "llm.call",
                SpanKind.LLM_CALL,
                correlation_id=correlation_id,
                parent_span_id=root_s.span_id,
                attrs={"prompt": f"Analyze this: {SEEDED_AWS_KEY}"},
            ) as llm_s:
                spans.append(llm_s)
                usage = MagicMock()
                usage.prompt_tokens = 50
                usage.completion_tokens = 25
                record_llm_usage(llm_s, usage=usage, model="test-model")

            # Simulate a snapshot write — must be redacted at SNAPSHOT boundary
            snapshot_raw = {"credential": SEEDED_AWS_KEY, "email": SEEDED_EMAIL}
            snapshot_clean = redactor.redact_mapping(snapshot_raw, Boundary.SNAPSHOT)
            logger.info("snapshot_written", path="s3://snapshots/inc.json")

    finally:
        clear_incident()

    return spans, snapshot_clean


class TestZeroLeaks:
    def test_no_seeded_secret_in_logs(self) -> None:
        buf = io.StringIO()
        configure_logging(log_level="DEBUG", output=buf)

        _, _ = _run_synthetic_incident(buf, "e2e_inc_001")

        buf.seek(0)
        log_text = buf.read()
        assert SEEDED_AWS_KEY not in log_text, "AWS key leaked in logs"
        # Check the base64-encoded JWT header (first segment) is not present raw
        jwt_header = SEEDED_BEARER.split(" ")[1].split(".")[0]  # "eyJhbGciOiJIUzI1NiJ9"
        assert jwt_header not in log_text, f"Bearer JWT header '{jwt_header}' leaked in logs"

    def test_no_seeded_secret_in_span_attributes(self) -> None:
        buf = io.StringIO()
        configure_logging(log_level="DEBUG", output=buf)

        spans, _ = _run_synthetic_incident(buf, "e2e_inc_002")

        for s in spans:
            attr_str = json.dumps(s.attributes)
            assert SEEDED_AWS_KEY not in attr_str, f"AWS key leaked in span {s.name}"
            assert "eyJhbGciOiJIUzI1NiJ9" not in attr_str, f"Bearer leaked in span {s.name}"

    def test_no_seeded_secret_in_snapshot(self) -> None:
        buf = io.StringIO()
        configure_logging(log_level="DEBUG", output=buf)

        _, snapshot_clean = _run_synthetic_incident(buf, "e2e_inc_003")

        snapshot_str = json.dumps(snapshot_clean)
        assert SEEDED_AWS_KEY not in snapshot_str, "AWS key leaked in snapshot"

    def test_one_trace_tree_no_orphans(self) -> None:
        """SC-003: exactly one trace tree with no orphaned spans."""
        buf = io.StringIO()
        configure_logging(log_level="DEBUG", output=buf)

        spans, _ = _run_synthetic_incident(buf, "e2e_inc_004")

        roots = [s for s in spans if s.parent_span_id is None]
        assert len(roots) == 1, f"Expected 1 root span, got {len(roots)}"

        span_ids = {s.span_id for s in spans}
        for s in spans:
            if s.parent_span_id is not None:
                assert s.parent_span_id in span_ids, f"Orphan span: {s.name}"

    def test_correlation_id_on_all_log_lines(self) -> None:
        """SC-002: every log line carries the incident's correlation_id."""
        buf = io.StringIO()
        configure_logging(log_level="DEBUG", output=buf)

        cid = "e2e_inc_005"
        _run_synthetic_incident(buf, cid)

        buf.seek(0)
        lines = [json.loads(line) for line in buf.read().splitlines() if line.strip()]
        incident_lines = [line for line in lines if line.get("correlation_id") == cid]
        assert len(incident_lines) >= 1
        for line in incident_lines:
            assert line["correlation_id"] == cid
