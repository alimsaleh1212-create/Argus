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
    def __init__(self, inc, ack_returns=True):
        self._inc = inc
        self.ack_called = False
        self.ack_returns = ack_returns

    async def get(self, _id):
        return self._inc

    async def acknowledge(self, _id, *, actor):
        self.ack_called = True
        return self.ack_returns


class _Audit:
    def __init__(self):
        self.append_called = False

    async def append(self, **kw):
        self.append_called = True
        return None


class _Sup:
    async def close_incident(self, _id, _repo, audit_repo=None, actor="operator"):
        return True


_OP = OperatorSession(subject="alice", role="admin", expires_at=datetime.now(UTC))


@pytest.mark.asyncio
async def test_acknowledge_ok():
    inc = _inc(IncidentStatus.ESCALATED)
    repo = _IncRepo(inc, ack_returns=True)
    audit = _Audit()
    out = await r.acknowledge_incident(inc.id, repo, audit, _OP)
    assert repo.ack_called is True
    assert out["status"] == "escalated"
    assert out["acknowledged"] is True
    assert audit.append_called is True


@pytest.mark.asyncio
async def test_acknowledge_already_acknowledged():
    inc = _inc(IncidentStatus.ESCALATED)
    repo = _IncRepo(inc, ack_returns=False)
    audit = _Audit()
    out = await r.acknowledge_incident(inc.id, repo, audit, _OP)
    assert repo.ack_called is True
    assert out["status"] == "escalated"
    assert out["acknowledged"] is False
    assert audit.append_called is False


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
