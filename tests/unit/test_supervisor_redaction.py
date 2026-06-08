"""Unit tests — T032: supervisor logs/spans contain no unredacted PII/secrets (SC-007).

Plants a synthetic secret in an incident and asserts it is not echoed
in any span attribute emitted by the supervisor (reuses the #2 scrubber).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.pipeline import StageName, StageOutcome, StageResult
from backend.infra.config import SupervisorSettings
from backend.infra.tracing import build_tracer
from backend.services.supervisor import Supervisor

_SECRET = "AKIAIOSFODNN7EXAMPLE"  # synthetic AWS-style key — scrubbed by #2 deterministic scrubber


def _incident_with_secret() -> Incident:
    """Incident whose evidence contains a planted secret."""
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.GROUNDED,
        severity=Severity.LOW,
        correlation_id="corr-redaction",
        dedup_fingerprint="fp-redaction",
        source="wazuh",
        raw_alert={"rule": {"level": 2}, "detail": _SECRET},
        evidence={"flags": [], "verdict": "test", "severity": "low", "summary": f"key={_SECRET}",
                  "normalized_event": {}, "retrieved_context": []},
    )


class RecordingRepo:
    """Repo that records all advance_status calls and captured span attributes."""

    def __init__(self, incident: Incident) -> None:
        self._incident = incident.model_copy(deep=True)
        self.advances: list[dict] = []

    async def get(self, incident_id: uuid.UUID) -> Incident | None:
        if self._incident.id == incident_id:
            return self._incident
        return None

    async def advance_status(self, incident_id, *, expected, target, disposition=None, evidence_patch=None) -> bool:
        if self._incident.id != incident_id or self._incident.status != expected:
            return False
        self.advances.append({"from": expected, "to": target, "disposition": disposition})
        self._incident = self._incident.model_copy(update={"status": target, "disposition": disposition})
        return True


class RecordingTracer:
    """Tracer that records all span attribute values for inspection."""

    def __init__(self) -> None:
        self.spans: list[dict[str, Any]] = []
        self._max_attr_bytes = 8192
        self._exporter = None

    def _queue_span(self, s: Any) -> None:
        self.spans.append({"name": s.name, "attributes": dict(s.attributes or {})})


@pytest.mark.asyncio
async def test_supervisor_spans_do_not_contain_planted_secret():
    """Span attributes emitted during run_incident must not contain the planted secret."""
    from backend.infra.tracing import span as _span

    incident = _incident_with_secret()
    repo = RecordingRepo(incident)

    recording_tracer = RecordingTracer()

    sv = Supervisor(
        stages={},  # fast-path (low severity) — no stage called
        cfg=SupervisorSettings(),
        tracer=recording_tracer,  # type: ignore[arg-type]
    )
    await sv.run_incident(incident.id, repo)

    for sp in recording_tracer.spans:
        for key, value in sp["attributes"].items():
            assert _SECRET not in str(value), (
                f"Unredacted secret found in span '{sp['name']}' attribute '{key}': {value}"
            )


@pytest.mark.asyncio
async def test_disposition_does_not_contain_raw_alert_data():
    """The disposition string stored on the incident is a known vocab word, not raw alert content."""
    incident = _incident_with_secret()
    repo = RecordingRepo(incident)
    sv = Supervisor(
        stages={},
        cfg=SupervisorSettings(),
        tracer=build_tracer(exporter=None),
    )
    await sv.run_incident(incident.id, repo)

    final = await repo.get(incident.id)
    if final.disposition:
        assert _SECRET not in final.disposition
