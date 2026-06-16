"""Unit tests for feedback outcome-fact mapping (US1, T004).

Tests written first — they exercise record_outcome_facts with a mocked memory
store and assert the fact shape/keying matches the contract.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.memory import EntityKind, EntityRef, TemporalFact
from backend.domain.redaction import Boundary
from backend.infra.config import FeedbackSettings


class _FakeRedactor:
    def redact_text(self, text: str, boundary: Boundary) -> str:
        return text

    def redact_mapping(self, data: dict[str, Any], boundary: Boundary) -> dict[str, Any]:
        return data


class _FakeStore:
    def __init__(self) -> None:
        self.facts: list[TemporalFact] = []

    async def write_fact(self, fact: TemporalFact) -> None:
        self.facts.append(fact)


def _incident_with_verification(
    verdict: str,
    results: list[dict],
    updated_at: datetime | None = None,
) -> Incident:
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.RESOLVED,
        severity=Severity.HIGH,
        correlation_id="corr",
        dedup_fingerprint="fp",
        source="wazuh",
        raw_alert={},
        evidence={
            "response": {
                "results": results,
                "verification": {"verdict": verdict},
            }
        },
        updated_at=updated_at or datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_record_outcome_facts_writes_one_fact_per_applied_target() -> None:
    from backend.services.memory import record_outcome_facts

    store = _FakeStore()
    cfg = FeedbackSettings()
    incident = _incident_with_verification(
        verdict="regressed",
        results=[
            {"type": "block_ip", "target": "10.0.0.1", "status": "applied"},
            {"type": "open_ticket", "target": "10.0.0.1", "status": "not_executed"},
            {"type": "isolate_host", "target": "server-01", "status": "applied"},
        ],
    )

    await record_outcome_facts(incident, store, _FakeRedactor(), cfg=cfg)

    assert len(store.facts) == 2
    values = {f.entity.value for f in store.facts}
    assert values == {"10.0.0.1", "server-01"}
    for fact in store.facts:
        assert fact.fact_type == "remediation_outcome"
        assert fact.value == "regressed"


@pytest.mark.asyncio
async def test_record_outcome_facts_uses_config_fact_type() -> None:
    from backend.services.memory import record_outcome_facts

    store = _FakeStore()
    cfg = FeedbackSettings(outcome_fact_type="outcome_v2")
    incident = _incident_with_verification(
        verdict="unverified",
        results=[{"type": "block_ip", "target": "1.2.3.4", "status": "applied"}],
    )

    await record_outcome_facts(incident, store, _FakeRedactor(), cfg=cfg)

    assert store.facts[0].fact_type == "outcome_v2"


@pytest.mark.asyncio
async def test_record_outcome_facts_no_verification_writes_nothing() -> None:
    from backend.services.memory import record_outcome_facts

    store = _FakeStore()
    cfg = FeedbackSettings()
    incident = _incident_with_verification(
        verdict="regressed",
        results=[{"type": "block_ip", "target": "1.2.3.4", "status": "applied"}],
    )
    incident.evidence = {"response": {"results": []}}  # no verification

    await record_outcome_facts(incident, store, _FakeRedactor(), cfg=cfg)
    assert store.facts == []


@pytest.mark.asyncio
async def test_record_outcome_facts_no_applied_targets_writes_nothing() -> None:
    from backend.services.memory import record_outcome_facts

    store = _FakeStore()
    cfg = FeedbackSettings()
    incident = _incident_with_verification(
        verdict="verified",
        results=[{"type": "open_ticket", "target": "x", "status": "not_executed"}],
    )

    await record_outcome_facts(incident, store, _FakeRedactor(), cfg=cfg)
    assert store.facts == []


@pytest.mark.asyncio
async def test_record_outcome_facts_key_matches_read_key() -> None:
    """Write-key == read-key: the same EntityRef value the consumers query."""
    from backend.services.memory import record_outcome_facts

    store = _FakeStore()
    cfg = FeedbackSettings()
    incident = _incident_with_verification(
        verdict="regressed",
        results=[{"type": "block_ip", "target": "203.0.113.5", "status": "applied"}],
    )

    await record_outcome_facts(incident, store, _FakeRedactor(), cfg=cfg)

    assert len(store.facts) == 1
    fact = store.facts[0]
    # The value is the key; kind must be resolvable and consistent.
    assert fact.entity.value == "203.0.113.5"
    assert fact.entity.kind in (EntityKind.ADDRESS, EntityKind.INDICATOR)


@pytest.mark.asyncio
async def test_record_outcome_facts_redacts_free_text() -> None:
    class _CensorRedactor:
        def redact_text(self, text: str, boundary: Boundary) -> str:
            return "[REDACTED]"

        def redact_mapping(self, data: dict[str, Any], boundary: Boundary) -> dict[str, Any]:
            return data

    from backend.services.memory import record_outcome_facts

    store = _FakeStore()
    cfg = FeedbackSettings()
    incident = _incident_with_verification(
        verdict="regressed",
        results=[{"type": "block_ip", "target": "secret-token-123", "status": "applied"}],
    )

    await record_outcome_facts(incident, store, _CensorRedactor(), cfg=cfg)

    assert store.facts[0].entity.value == "[REDACTED]"
