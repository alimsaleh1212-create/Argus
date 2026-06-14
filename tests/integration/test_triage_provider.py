"""T015 — Integration test: triage handler against a real LlmClient.

Marked as integration — requires a running LLM provider (Gemini or Ollama).
Run with: uv run pytest -m integration tests/integration/test_triage_provider.py
"""

from __future__ import annotations

import uuid

import pytest

from backend.agents.triage import make_triage_handler
from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.pipeline import StageOutcome
from backend.infra.config import TriageSettings


def _incident() -> Incident:
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.TRIAGING,
        severity=Severity.MEDIUM,
        correlation_id="integration-triage-test",
        dedup_fingerprint="integration-triage-fp",
        source="wazuh",
        raw_alert={},
        evidence={
            "verdict": "suspicious",
            "severity": "medium",
            "normalized_event": {
                "rule_id": "5763",
                "rule_description": "SSH brute force attack then success",
                "src_ip": "198.51.100.42",
                "user": "root",
                "event_count": 500,
            },
            "summary": "500 failed SSH logins followed by a successful root login from an unknown IP.",
            "retrieved_context": None,
        },
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_triage_handler_returns_schema_valid_judgment(real_llm_client):
    """Handler makes exactly one call and returns a schema-valid StageResult."""
    handler = make_triage_handler(real_llm_client, TriageSettings())
    result = await handler(_incident())

    assert result.stage.value == "triage"
    assert result.outcome in (StageOutcome.ADVANCE, StageOutcome.RESOLVED, StageOutcome.ESCALATE)
    assert result.tokens_consumed > 0, "Provider must report non-zero token usage"
    assert result.evidence_patch is not None
    assert "triage" in result.evidence_patch

    triage = result.evidence_patch["triage"]
    assert triage["verdict"] in ("real", "noise", "uncertain")
    assert 0.0 <= triage["confidence"] <= 1.0
    assert len(triage["rationale"]) > 0
    assert len(triage["cited_evidence"]) >= 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_triage_handler_reports_nonzero_tokens(real_llm_client):
    """Tokens consumed feeds the supervisor cap (FR-009, SC-006)."""
    handler = make_triage_handler(real_llm_client, TriageSettings())
    result = await handler(_incident())
    assert result.tokens_consumed > 0
