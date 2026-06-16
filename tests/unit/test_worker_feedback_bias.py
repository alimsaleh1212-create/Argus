"""Unit tests for worker feedback-bias evidence patch (US2 / US3, T008/T020)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from backend.domain.incident import Evidence, Incident, IncidentStatus, NormalizedEvent, Severity
from backend.domain.memory import EntityKind, EntityRef, FactState, TemporalFact
from backend.domain.redaction import Boundary
from backend.infra.config import FeedbackSettings


class _FakeRedactor:
    def redact_text(self, text: str, boundary: Boundary) -> str:
        return text

    def redact_mapping(self, data: dict[str, Any], boundary: Boundary) -> dict[str, Any]:
        return dict(data)


class _FakeStore:
    def __init__(self, facts: list[TemporalFact] | None = None) -> None:
        self.facts = facts or []

    async def query_fact(
        self,
        entity: EntityRef,
        fact_type: str,
        *,
        as_of: datetime | None = None,
    ) -> FactState:
        matches = [
            f for f in self.facts if f.entity.value == entity.value and f.fact_type == fact_type
        ]
        return FactState(fact=matches[0] if matches else None, is_current=bool(matches))

    async def write_fact(self, fact: TemporalFact) -> None:
        self.facts.append(fact)


def _fact(value: str, outcome: str = "regressed") -> TemporalFact:
    return TemporalFact(
        entity=EntityRef(kind=EntityKind.ADDRESS, value=value),
        fact_type="remediation_outcome",
        value=outcome,
        valid_from=datetime.now(UTC),
    )


def _incident(*, severity: Severity = Severity.MEDIUM) -> Incident:
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.GROUNDED,
        severity=severity,
        correlation_id="corr",
        dedup_fingerprint="fp",
        source="wazuh",
        raw_alert={},
        normalized_event={
            "agent_ip": "10.0.0.1",
            "agent_name": "host-a",
            "fields": {},
        },
        evidence={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


class _SimpleObj:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


@pytest.mark.asyncio
async def test_apply_feedback_bias_sets_bias_applied_on_severity_bump() -> None:
    from backend.worker import _apply_feedback_bias

    store = _FakeStore(facts=[_fact("10.0.0.1")])
    incident = _incident(severity=Severity.MEDIUM)
    settings = _SimpleObj(
        feedback=FeedbackSettings(severity_bias="bump_one", enabled=True),
        observability=_SimpleObj(presidio_enabled=False),
    )

    evidence = Evidence(
        verdict="real",
        severity=Severity.MEDIUM,
        normalized_event=NormalizedEvent(),
        summary="test",
        flags=[],
    )
    result = await _apply_feedback_bias(evidence, incident, store, settings)

    assert result.severity == Severity.HIGH
    assert "prior_failure" in result.flags
    assert result.model_dump(mode="json").get("feedback") == {"bias_applied": True}


@pytest.mark.asyncio
async def test_apply_feedback_bias_sets_bias_applied_on_prior_failure_flag_only() -> None:
    from backend.worker import _apply_feedback_bias

    # severity already critical, so only the flag changes.
    store = _FakeStore(facts=[_fact("10.0.0.1")])
    incident = _incident(severity=Severity.CRITICAL)
    settings = _SimpleObj(
        feedback=FeedbackSettings(severity_bias="bump_one", enabled=True),
        observability=_SimpleObj(presidio_enabled=False),
    )

    evidence = Evidence(
        verdict="real",
        severity=Severity.CRITICAL,
        normalized_event=NormalizedEvent(),
        summary="test",
        flags=[],
    )
    result = await _apply_feedback_bias(evidence, incident, store, settings)

    assert result.severity == Severity.CRITICAL
    assert "prior_failure" in result.flags
    assert result.model_dump(mode="json").get("feedback") == {"bias_applied": True}


@pytest.mark.asyncio
async def test_apply_feedback_bias_no_bias_when_no_signals() -> None:
    from backend.worker import _apply_feedback_bias

    store = _FakeStore(facts=[])
    incident = _incident(severity=Severity.MEDIUM)
    settings = _SimpleObj(
        feedback=FeedbackSettings(enabled=True),
        observability=_SimpleObj(presidio_enabled=False),
    )

    evidence = Evidence(
        verdict="real",
        severity=Severity.MEDIUM,
        normalized_event=NormalizedEvent(),
        summary="test",
        flags=[],
    )
    result = await _apply_feedback_bias(evidence, incident, store, settings)

    assert result.severity == Severity.MEDIUM
    assert result.feedback is None
