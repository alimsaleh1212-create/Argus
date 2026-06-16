"""Integration tests: GET /incidents queue + detail + audit (#12 T026).

Uses mocked repositories so no Postgres connection is needed.
Auth uses the same pattern as test_auth_api.py.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from backend.domain.dashboard import IncidentSummary

_SALT = "intsalt"
_ITERATIONS = 1000
_PASSWORD = "integrationpass"
_HASH = hashlib.pbkdf2_hmac("sha256", _PASSWORD.encode(), _SALT.encode(), _ITERATIONS).hex()
_JWT_SECRET = "int-jwt-secret"

_INC_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_NOW = datetime(2026, 6, 12, 9, 0, 0, tzinfo=UTC)


def _make_summary(**overrides) -> IncidentSummary:
    defaults = {
        "id": _INC_ID,
        "status": "triaging",
        "severity": "high",
        "disposition": None,
        "source": "wazuh",
        "summary": "Suspicious login",
        "is_awaiting_approval": False,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    return IncidentSummary(**{**defaults, **overrides})


def _make_auth_service():
    from backend.services.auth import AuthService

    return AuthService(
        admin_username="admin",
        password_hash=_HASH,
        salt=_SALT,
        iterations=_ITERATIONS,
        jwt_secret=_JWT_SECRET,
        algorithm="HS256",
        token_ttl_minutes=60,
    )


def _make_app(
    *,
    queue_items: list[IncidentSummary] | None = None,
    queue_total: int | None = None,
    incident=None,
    audit_rows=None,
):
    from backend.dependencies import (
        get_approval_repo,
        get_audit_repo,
        get_auth_service,
        get_incident_repo,
        get_redactor_dep,
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

    _queue_items = queue_items if queue_items is not None else [_make_summary()]
    _queue_total = queue_total if queue_total is not None else len(_queue_items)

    async def fake_incident_repo():
        repo = AsyncMock()
        repo.list_for_queue = AsyncMock(return_value=_queue_items)
        repo.count_for_queue = AsyncMock(return_value=_queue_total)
        if incident is not None:
            repo.get = AsyncMock(return_value=incident)
        else:
            repo.get = AsyncMock(return_value=None)
        yield repo

    async def fake_audit_repo():
        repo = AsyncMock()
        repo.list_for_incident = AsyncMock(return_value=audit_rows or [])
        yield repo

    async def fake_approval_repo():
        repo = AsyncMock()
        repo.get_pending_for_incident = AsyncMock(return_value=None)
        yield repo

    class _FakeRedactor:
        def redact_text(self, text, boundary):
            return text

        def redact_mapping(self, data, boundary):
            return dict(data)

    app.dependency_overrides[get_incident_repo] = fake_incident_repo
    app.dependency_overrides[get_audit_repo] = fake_audit_repo
    app.dependency_overrides[get_approval_repo] = fake_approval_repo
    app.dependency_overrides[get_redactor_dep] = lambda: _FakeRedactor()

    return app, auth_svc


def _get_token(app) -> str:
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/auth/login", json={"username": "admin", "password": _PASSWORD})
    return resp.json()["access_token"]


@pytest.mark.integration
class TestQueueAuth:
    def test_no_token_returns_401(self) -> None:
        app, _ = _make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/incidents")
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self) -> None:
        app, _ = _make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/incidents", headers={"Authorization": "Bearer bad.token.here"})
        assert resp.status_code == 401


@pytest.mark.integration
class TestQueueEndpoint:
    def test_empty_queue_returns_200_with_zero_total(self) -> None:
        app, _ = _make_app(queue_items=[], queue_total=0)
        token = _get_token(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/incidents", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_queue_returns_summary_fields(self) -> None:
        app, _ = _make_app(queue_items=[_make_summary()])
        token = _get_token(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/incidents", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        item = items[0]
        assert item["id"] == str(_INC_ID)
        assert item["status"] == "triaging"
        assert item["severity"] == "high"
        assert item["source"] == "wazuh"
        assert item["is_awaiting_approval"] is False

    def test_queue_applied_filters_reflected(self) -> None:
        app, _ = _make_app(queue_items=[], queue_total=0)
        token = _get_token(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get(
                "/incidents?view=resolved&status=escalated&severity=critical",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["view"] == "resolved"
        assert "escalated" in body["applied_filters"]["status"]
        assert "critical" in body["applied_filters"]["severity"]

    def test_limit_offset_in_response(self) -> None:
        app, _ = _make_app(queue_items=[], queue_total=0)
        token = _get_token(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get(
                "/incidents?limit=10&offset=20",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["limit"] == 10
        assert body["offset"] == 20

    def test_limit_over_200_returns_422(self) -> None:
        app, _ = _make_app()
        token = _get_token(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get(
                "/incidents?limit=201",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 422

    def test_invalid_view_returns_422(self) -> None:
        app, _ = _make_app()
        token = _get_token(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get(
                "/incidents?view=badview",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 422


@pytest.mark.integration
class TestDetailEndpoint:
    def test_unknown_incident_returns_404(self) -> None:
        app, _ = _make_app(incident=None)
        token = _get_token(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get(
                f"/incidents/{uuid.uuid4()}",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 404

    def test_known_incident_returns_detail(self) -> None:
        from backend.domain.incident import Incident, IncidentStatus, Severity

        fake_inc = Incident(
            id=_INC_ID,
            status=IncidentStatus.TRIAGING,
            severity=Severity.HIGH,
            correlation_id="corr-123",
            dedup_fingerprint="fp-001",
            source="wazuh",
            raw_alert={},
            evidence={"summary": "Suspicious login attempt", "verdict": "real"},
            created_at=_NOW,
            updated_at=_NOW,
        )
        app, _ = _make_app(incident=fake_inc)
        token = _get_token(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get(
                f"/incidents/{_INC_ID}",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(_INC_ID)
        assert body["status"] == "triaging"
        assert body["summary"] == "Suspicious login attempt"
        assert body["correlation_id"] == "corr-123"
        assert body["pending_approval"] is None
        assert body["audit"] == []

    def test_no_token_on_detail_returns_401(self) -> None:
        app, _ = _make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get(f"/incidents/{_INC_ID}")
        assert resp.status_code == 401

    def test_feedback_prior_outcome_is_redacted_in_detail(self) -> None:
        from backend.dependencies import get_redactor_dep
        from backend.domain.incident import Incident, IncidentStatus, Severity

        planted = "AKIAIOSFODNN7EXAMPLE"
        fake_inc = Incident(
            id=_INC_ID,
            status=IncidentStatus.TRIAGING,
            severity=Severity.HIGH,
            correlation_id="corr-123",
            dedup_fingerprint="fp-001",
            source="wazuh",
            raw_alert={},
            evidence={
                "summary": "Suspicious login attempt",
                "verdict": "real",
                "prior_outcome": {
                    "signals": [
                        {
                            "indicator": f"ip:{planted}",
                            "outcome": "failed",
                            "is_current": True,
                        }
                    ],
                    "biased_severity": "critical",
                },
            },
            created_at=_NOW,
            updated_at=_NOW,
        )

        class _ScrubbingRedactor:
            def redact_text(self, text, boundary):
                return text.replace(planted, "[REDACTED:CREDENTIAL]")

            def redact_mapping(self, data, boundary):
                import json

                return json.loads(self.redact_text(json.dumps(data), boundary))

        app, _ = _make_app(incident=fake_inc)
        app.dependency_overrides[get_redactor_dep] = lambda: _ScrubbingRedactor()
        token = _get_token(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get(
                f"/incidents/{_INC_ID}",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        evidence = resp.json()["evidence"]
        assert planted not in str(evidence["prior_outcome"])
        assert "[REDACTED:CREDENTIAL]" in str(evidence["prior_outcome"])


@pytest.mark.integration
class TestAuditEndpoint:
    def test_unknown_incident_returns_404(self) -> None:
        app, _ = _make_app(incident=None)
        token = _get_token(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get(
                f"/incidents/{uuid.uuid4()}/audit",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 404

    def test_known_incident_returns_audit_list(self) -> None:
        from backend.domain.incident import Incident, IncidentStatus, Severity
        from backend.repositories.audit import AuditRow

        fake_inc = Incident(
            id=_INC_ID,
            status=IncidentStatus.RESOLVED,
            severity=Severity.MEDIUM,
            correlation_id="corr-456",
            dedup_fingerprint="fp-002",
            source="wazuh",
            raw_alert={},
            created_at=_NOW,
            updated_at=_NOW,
        )
        audit_row = AuditRow(
            id=1,
            incident_id=_INC_ID,
            actor="admin",
            action="open_ticket",
            target="INC-001",
            outcome="applied",
            idempotency_key="idem-001",
            created_at=_NOW,
        )
        app, _ = _make_app(incident=fake_inc, audit_rows=[audit_row])
        token = _get_token(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get(
                f"/incidents/{_INC_ID}/audit",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["audit"]) == 1
        assert body["audit"][0]["actor"] == "admin"
        assert body["audit"][0]["action"] == "open_ticket"
        assert body["audit"][0]["outcome"] == "applied"
