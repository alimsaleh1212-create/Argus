import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from backend.domain.dashboard import OperatorSession
from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.routers import incidents as r


def _inc(status):
    now = datetime.now(UTC)
    return Incident(
        id=uuid.uuid4(),
        status=status,
        severity=Severity.HIGH,
        correlation_id="c",
        dedup_fingerprint="f",
        source="wazuh",
        raw_alert={},
        normalized_event={},
        evidence={},
        disposition="escalated_triage",
        attempts=0,
        created_at=now,
        updated_at=now,
    )


class _IncRepo:
    def __init__(self, inc):
        self._inc = inc
        self.ack = False

    async def get(self, _id):
        return self._inc

    async def acknowledge(self, _id, *, actor):
        self.ack = True
        return True


class _Audit:
    async def append(self, **kw):
        return None


class _Sup:
    async def close_incident(self, _id, _repo, audit_repo=None, actor="operator"):
        return True


_OP = OperatorSession(subject="alice", role="admin", expires_at=datetime.now(UTC))


@pytest.mark.asyncio
async def test_acknowledge_ok():
    inc = _inc(IncidentStatus.ESCALATED)
    repo = _IncRepo(inc)
    out = await r.acknowledge_incident(inc.id, repo, _Audit(), _OP)
    assert repo.ack is True and out["status"] == "escalated"


@pytest.mark.asyncio
async def test_resolve_rejects_non_escalated():
    inc = _inc(IncidentStatus.TRIAGING)
    with pytest.raises(HTTPException) as e:
        await r.resolve_incident(inc.id, _IncRepo(inc), _Audit(), _Sup(), _OP)
    assert e.value.status_code == 409


@pytest.mark.asyncio
async def test_resolve_ok():
    inc = _inc(IncidentStatus.ESCALATED)
    out = await r.resolve_incident(inc.id, _IncRepo(inc), _Audit(), _Sup(), _OP)
    assert out["status"] == "resolved" and out["disposition"] == "operator_resolved"
