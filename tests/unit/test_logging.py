"""Unit tests — T012: Structured, correlated, redacted logging (US2).

Tests the structlog chain additions from logging.py:
- Every line is structured (JSON key/value, FR-008)
- Lines carry the bound correlation_id (FR-009)
- Seeded secrets/PII never appear raw in rendered output (FR-010)
- A line emitted with no incident context renders correlation_id="-" (FR-011)
- Processor error drops the offending field (fail-closed), not the whole line
"""

from __future__ import annotations

import io
import json

import pytest
import structlog

from backend.infra.logging import bind_incident, clear_incident, configure_logging, get_logger

FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
FAKE_EMAIL = "charlie@example.com"


@pytest.fixture(autouse=True)
def reset_structlog():
    """Ensure each test gets a fresh structlog config and clean contextvars."""
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()


@pytest.fixture
def log_capture():
    """Configure logging to an in-memory buffer; return the buffer."""
    buf = io.StringIO()
    configure_logging(log_level="DEBUG", output=buf)
    return buf


def _lines(buf: io.StringIO) -> list[dict]:
    buf.seek(0)
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


class TestStructuredOutput:
    def test_emitted_line_is_json(self, log_capture: io.StringIO) -> None:
        logger = get_logger("test")
        logger.info("hello world")
        lines = _lines(log_capture)
        assert len(lines) >= 1
        line = lines[-1]
        assert "event" in line
        assert "level" in line
        assert "timestamp" in line

    def test_event_field_present(self, log_capture: io.StringIO) -> None:
        logger = get_logger("test")
        logger.info("my_event", extra_field="value")
        lines = _lines(log_capture)
        assert lines[-1]["event"] == "my_event"


class TestCorrelationId:
    def test_bound_incident_propagates_to_log_line(self, log_capture: io.StringIO) -> None:
        bind_incident("inc_abc123")
        logger = get_logger("test")
        logger.info("inside incident")
        lines = _lines(log_capture)
        assert lines[-1]["correlation_id"] == "inc_abc123"

    def test_filtering_by_correlation_id(self, log_capture: io.StringIO) -> None:
        bind_incident("inc_001")
        get_logger("test").info("event A")
        clear_incident()
        bind_incident("inc_002")
        get_logger("test").info("event B")
        clear_incident()

        lines = _lines(log_capture)
        inc001_lines = [line for line in lines if line.get("correlation_id") == "inc_001"]
        inc002_lines = [line for line in lines if line.get("correlation_id") == "inc_002"]
        assert len(inc001_lines) >= 1
        assert len(inc002_lines) >= 1
        # Each incident's lines only contain its own id
        for line in inc001_lines:
            assert line["correlation_id"] == "inc_001"

    def test_no_incident_context_renders_dash(self, log_capture: io.StringIO) -> None:
        # No bind_incident call
        logger = get_logger("test")
        logger.info("startup event")
        lines = _lines(log_capture)
        last = lines[-1]
        assert last.get("correlation_id") == "-" or last.get("no_incident") is True

    def test_no_incident_context_does_not_raise(self, log_capture: io.StringIO) -> None:
        # Should not raise even without a bound incident
        get_logger("test").info("background task")


class TestRedactionInChain:
    def test_aws_key_in_log_field_not_present_raw(self, log_capture: io.StringIO) -> None:
        bind_incident("inc_x")
        logger = get_logger("test")
        logger.info("processing", payload=f"key={FAKE_AWS_KEY}")
        lines = _lines(log_capture)
        output = json.dumps(lines[-1])
        assert FAKE_AWS_KEY not in output

    def test_processor_error_drops_field_not_whole_line(
        self, log_capture: io.StringIO, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If _redact_str raises on a specific value, that field gets [REDACTION-FAILED]
        while the rest of the line is still emitted (per-field fail-closed, FR-003)."""
        import backend.infra.redaction as redaction_mod

        original_redact_str = redaction_mod._redact_str

        def _raise_for_boom(text, boundary):
            if text == "boom":
                raise RuntimeError("scrubber exploded on this value")
            return original_redact_str(text, boundary)

        monkeypatch.setattr(redaction_mod, "_redact_str", _raise_for_boom)

        bind_incident("inc_y")
        get_logger("test").info("ok event", bad_field="boom", good_field="fine")
        lines = _lines(log_capture)
        last = lines[-1]
        # The line must still be emitted
        assert last.get("event") == "ok event"
        # good_field survived intact
        assert last.get("good_field") == "fine"
        # bad_field was replaced with the fail-closed marker, not the raw value
        assert last.get("bad_field") == "[REDACTION-FAILED]"
        assert "boom" not in str(last)
