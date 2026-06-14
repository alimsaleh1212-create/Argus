"""Unit tests — note/preview contains no unredacted sensitive values (T027)."""

from __future__ import annotations

import json
import uuid

import pytest

from backend.agents.enrichment import make_enrichment_handler
from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.llm import LlmResponse, ProviderId, StopReason, TokenUsage
from backend.infra.config import EnrichmentSettings

_SECRET = "AKIAIOSFODNN7EXAMPLE"
_PII = "alice@example.com"


def _incident_with_secret() -> Incident:
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.ENRICHING,
        severity=Severity.HIGH,
        correlation_id="corr-redaction",
        dedup_fingerprint="fp-redaction",
        source="wazuh",
        raw_alert={},
        evidence={
            "verdict": "suspicious",
            "severity": "high",
            "normalized_event": {
                "rule_id": "9999",
                "rule_description": f"Event involving {_PII} and key={_SECRET}",
                "rule_groups": [],
                "fields": {"secret_key": _SECRET, "email": _PII},
            },
            "summary": f"Sensitive test: email={_PII} key={_SECRET}",
        },
    )


class FakeLlm:
    def __init__(self, summary: str):
        self._summary = summary

    async def generate(self, request, *, correlation_id=None):
        return LlmResponse(
            content=json.dumps(
                {
                    "assessment": "confirmed",
                    "confidence": 0.8,
                    "correlation_summary": self._summary,
                    "cited_evidence": ["rule_id=9999"],
                }
            ),
            usage=TokenUsage(prompt_tokens=20, completion_tokens=10),
            model="fake",
            provider=ProviderId.GEMINI,
            stop_reason=StopReason.END_TURN,
        )


class InjectionCorpus:
    """Returns corpus content with a planted secret."""

    async def search_reference(self, query, *, k: int) -> list:
        from backend.domain.corpus import ReferenceCorpusEntry, ReferenceHit, ReferenceKind

        return [
            ReferenceHit(
                entry=ReferenceCorpusEntry(
                    kind=ReferenceKind.TECHNIQUE,
                    key="T9999",
                    title="Test entry",
                    content=f"Context references {_SECRET}",
                ),
                relevance=0.8,
                matched_on="technique",
            )
        ]


@pytest.mark.asyncio
async def test_note_does_not_contain_secret_from_evidence():
    """StageResult.note (the ≤200-char preview) must not expose the planted secret."""
    # The secret should NOT appear in the note since we don't put raw evidence in note
    llm = FakeLlm("Signals aligned on technique match.")
    handler = make_enrichment_handler(llm, None, None, None, EnrichmentSettings())
    result = await handler(_incident_with_secret())

    note = result.note or ""
    assert _SECRET not in note, f"Secret leaked into note: {note!r}"


@pytest.mark.asyncio
async def test_note_does_not_contain_secret_from_retrieved_context():
    """Note built from correlation_summary only — retrieved secrets don't flow into note."""
    # Even if corpus content has a secret, the note comes from correlation_summary
    llm = FakeLlm("Technique T9999 corroborated by prior incidents.")
    handler = make_enrichment_handler(llm, InjectionCorpus(), None, None, EnrichmentSettings())
    result = await handler(_incident_with_secret())

    note = result.note or ""
    assert _SECRET not in note, f"Secret from retrieved context leaked into note: {note!r}"
