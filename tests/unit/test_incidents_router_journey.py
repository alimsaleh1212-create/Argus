import uuid
from datetime import UTC, datetime

import pytest

from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.routers import incidents as incidents_router


class _FakeIncidentRepo:
    def __init__(self, incident):
        self._incident = incident

    async def get(self, _id):
        return self._incident


class _FakeAuditRepo:
    async def list_for_incident(self, _id):
        return []


class _FakeApprovalRepo:
    async def get_pending_for_incident(self, _id):
        return None


class _NoopRedactor:
    def redact_mapping(self, mapping, _boundary):
        return mapping


@pytest.mark.asyncio
async def test_get_incident_includes_journey():
    now = datetime.now(UTC)
    inc = Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.ESCALATED,
        severity=Severity.HIGH,
        correlation_id="c",
        dedup_fingerprint="f",
        source="wazuh",
        raw_alert={},
        normalized_event={},
        evidence={"triage": {"verdict": "uncertain", "confidence": 0.4}},
        disposition="escalated_triage",
        attempts=0,
        created_at=now,
        updated_at=now,
    )
    view = await incidents_router.get_incident(
        inc.id, _FakeIncidentRepo(inc), _FakeAuditRepo(), _FakeApprovalRepo(), _NoopRedactor()
    )
    assert [s.stage for s in view.journey] == ["intake", "triage", "terminal"]
    assert view.journey[-1].outcome == "escalated"
