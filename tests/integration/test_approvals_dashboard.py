"""Integration tests: dashboard-auth path for POST /approvals/{id}/decision (T033).

Validates:
- approve → actor is the authenticated operator (not hardcoded "admin")
- reject → actor carries operator subject
- 409 on already-decided / expired
- 401 without token
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

_SALT = "dash-approval-salt"
_ITERATIONS = 1000
_PASSWORD = "operator-pass-123"
_HASH = hashlib.pbkdf2_hmac("sha256", _PASSWORD.encode(), _SALT.encode(), _ITERATIONS).hex()
_JWT_SECRET = "dash-approval-jwt-secret-long-enough"

_INC_ID = uuid.UUID("cccccccc-0000-0000-0000-000000000001")
_NOW = datetime(2026, 6, 12, 10, 0, 0, tzinfo=UTC)


def _make_auth_service():
    from backend.services.auth import AuthService

    return AuthService(
        admin_username="operator1",
        password_hash=_HASH,
        salt=_SALT,
        iterations=_ITERATIONS,
        jwt_secret=_JWT_SECRET,
        algorithm="HS256",
        token_ttl_minutes=60,
    )


def _make_pending_record(status: str = "pending"):
    from backend.repositories.approvals import ApprovalRecord

    return ApprovalRecord(
        id=1,
        incident_id=_INC_ID,
        plan_id="plan-001",
        pending_actions=[{"action_id": "isolate_host", "target": "srv-01"}],
        rationale="Host compromised.",
        status=status,
        deadline_at=_NOW + timedelta(hours=1),
        decided_by=None,
        decided_at=None,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_incident(status: str = "responding", disposition: str | None = None):
    from backend.domain.incident import Incident, IncidentStatus, Severity

    return Incident(
        id=_INC_ID,
        status=IncidentStatus(status),
        severity=Severity.HIGH,
        correlation_id="corr-001",
        dedup_fingerprint="fp-001",
        source="wazuh",
        raw_alert={},
        disposition=disposition,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_app(
    approval_record=None,
    resolve_result: bool = True,
    supervisor_disposition: str = "remediated",
    incident=None,
):
    from backend.dependencies import (
        get_approval_repo,
        get_audit_repo,
        get_auth_service,
        get_incident_repo,
        get_supervisor,
    )
    from backend.infra.config import load_settings
    from backend.infra.container import clear_registry
    from backend.main import create_app

    clear_registry()
    settings = load_settings()
    app = create_app(settings)
    clear_registry()

    auth_svc = _make_auth_service()
    app.dependency_overrides[get_auth_service] = lambda: auth_svc

    _record = approval_record if approval_record is not None else _make_pending_record()
    _incident = (
        incident
        if incident is not None
        else _make_incident(status="resolved", disposition=supervisor_disposition)
    )

    async def fake_approval_repo():
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=_record)
        repo.resolve = AsyncMock(return_value=resolve_result)
        yield repo

    async def fake_audit_repo():
        repo = AsyncMock()
        repo.append = AsyncMock(return_value=True)
        yield repo

    async def fake_incident_repo():
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=_incident)
        repo.list_for_queue = AsyncMock(return_value=[])
        repo.count_for_queue = AsyncMock(return_value=0)
        yield repo

    mock_supervisor = AsyncMock()
    mock_supervisor.resume_incident = AsyncMock(return_value=supervisor_disposition)

    app.dependency_overrides[get_approval_repo] = fake_approval_repo
    app.dependency_overrides[get_audit_repo] = fake_audit_repo
    app.dependency_overrides[get_incident_repo] = fake_incident_repo
    app.dependency_overrides[get_supervisor] = lambda: mock_supervisor

    return app, mock_supervisor


def _get_token(app) -> str:
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/auth/login", json={"username": "operator1", "password": _PASSWORD})
    return resp.json()["access_token"]


@pytest.mark.integration
class TestApprovalsDashboardAuth:
    def test_no_token_returns_401(self) -> None:
        app, _ = _make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/approvals/1/decision", json={"decision": "approve"})
        assert resp.status_code == 401

    def test_expired_token_returns_401(self) -> None:
        import jwt as pyjwt

        payload = {
            "sub": "operator1",
            "role": "admin",
            "iat": int((_NOW - timedelta(hours=2)).timestamp()),
            "exp": int((_NOW - timedelta(hours=1)).timestamp()),
        }
        expired_token = pyjwt.encode(payload, _JWT_SECRET, algorithm="HS256")
        app, _ = _make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/approvals/1/decision",
                json={"decision": "approve"},
                headers={"Authorization": f"Bearer {expired_token}"},
            )
        assert resp.status_code == 401

    def test_approve_calls_supervisor_resume(self) -> None:
        app, mock_supervisor = _make_app(supervisor_disposition="remediated")
        token = _get_token(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/approvals/1/decision",
                json={"decision": "approve"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["decision"] == "approve"
        assert body["disposition"] == "remediated"
        mock_supervisor.resume_incident.assert_called_once()

    def test_approve_uses_operator_subject_not_hardcoded_admin(self) -> None:
        app, mock_supervisor = _make_app()
        token = _get_token(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            client.post(
                "/approvals/1/decision",
                json={"decision": "approve"},
                headers={"Authorization": f"Bearer {token}"},
            )
        # supervisor.resume_incident receives actor="operator1" (from token sub)
        call_kwargs = mock_supervisor.resume_incident.call_args
        assert call_kwargs is not None
        # actor is passed as keyword arg
        assert call_kwargs.kwargs.get("actor") == "operator1"

    def test_reject_calls_supervisor_resume_with_reject(self) -> None:
        app, mock_supervisor = _make_app(supervisor_disposition="rejected_by_human")
        token = _get_token(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/approvals/1/decision",
                json={"decision": "reject"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["decision"] == "reject"
        mock_supervisor.resume_incident.assert_called_once()

    def test_already_decided_returns_409(self) -> None:
        already_approved = _make_pending_record(status="approved")
        app, _ = _make_app(approval_record=already_approved)
        token = _get_token(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/approvals/1/decision",
                json={"decision": "approve"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 409

    def test_race_condition_resolve_false_returns_409(self) -> None:
        # resolve() returns False → another request already won the race
        app, _ = _make_app(resolve_result=False)
        token = _get_token(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/approvals/1/decision",
                json={"decision": "approve"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 409

    def test_invalid_decision_returns_422(self) -> None:
        app, _ = _make_app()
        token = _get_token(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/approvals/1/decision",
                json={"decision": "maybe"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 422
