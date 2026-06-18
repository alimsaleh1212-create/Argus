"""Unit tests — Task A3: Supervisor.close_incident (operator-driven manual close).

Verifies:
- ESCALATED → RESOLVED (operator_resolved) when the guard holds, with an audit row.
- Guard lost (advance_status returns False) → returns False, no audit row.
"""

from __future__ import annotations

import uuid

import pytest

from backend.domain.incident import IncidentStatus
from backend.infra.config import SupervisorSettings
from backend.infra.tracing import build_tracer
from backend.services.supervisor import DISP_OPERATOR_RESOLVED, Supervisor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_supervisor(cfg: SupervisorSettings | None = None) -> Supervisor:
    return Supervisor(
        stages={},
        cfg=cfg or SupervisorSettings(),
        tracer=build_tracer(exporter=None),
    )


class _Repo:
    def __init__(self, guard_fails: bool = False) -> None:
        self.calls: list[tuple] = []
        self._guard_fails = guard_fails

    async def advance_status(
        self,
        incident_id: uuid.UUID,
        *,
        expected: IncidentStatus,
        target: IncidentStatus,
        disposition: str | None = None,
        evidence_patch: dict | None = None,
    ) -> bool:
        self.calls.append((expected, target, disposition))
        if self._guard_fails:
            return False
        return expected == IncidentStatus.ESCALATED


class _Audit:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def append(self, **kw) -> None:
        self.rows.append(kw)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_incident_escalated_to_resolved():
    sup = _make_supervisor()
    repo, audit = _Repo(), _Audit()

    ok = await sup.close_incident(uuid.uuid4(), repo, audit_repo=audit, actor="alice")

    assert ok is True
    assert repo.calls[0] == (
        IncidentStatus.ESCALATED,
        IncidentStatus.RESOLVED,
        DISP_OPERATOR_RESOLVED,
    )
    assert audit.rows and audit.rows[0]["action"] == "operator_resolved"


@pytest.mark.asyncio
async def test_close_incident_guard_lost_returns_false():
    sup = _make_supervisor()
    repo, audit = _Repo(guard_fails=True), _Audit()

    ok = await sup.close_incident(uuid.uuid4(), repo, audit_repo=audit, actor="alice")

    assert ok is False
    assert audit.rows == []
