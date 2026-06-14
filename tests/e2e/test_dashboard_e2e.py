"""Backend e2e: dashboard auth + queue + detail + audit (T031).

Tests the full dashboard API flow end-to-end with mocked services:
  1. POST /auth/login → get JWT
  2. GET /incidents (queue, with seeded data)
  3. GET /incidents/{id} (detail with evidence + audit)
  4. GET /incidents/{id}/audit (audit trail)

All protected endpoints reject unauthenticated requests (401).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

_SALT = "e2e-salt-string"
_ITERATIONS = 1000
_PASSWORD = "e2e-operator-pass"
_HASH = hashlib.pbkdf2_hmac("sha256", _PASSWORD.encode(), _SALT.encode(), _ITERATIONS).hex()
_JWT_SECRET = "e2e-jwt-signing-secret-long-enough"

_INC_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
_NOW = datetime(2026, 6, 12, 9, 0, 0, tzinfo=UTC)


def _make_auth_service():
    from backend.services.auth import AuthService

    return AuthService(
        admin_username="operator",
        password_hash=_HASH,
        salt=_SALT,
        iterations=_ITERATIONS,
        jwt_secret=_JWT_SECRET,
        algorithm="HS256",
        token_ttl_minutes=60,
    )


def _seed_incident():
    from backend.domain.incident import Incident, IncidentStatus, Severity

    return Incident(
        id=_INC_ID,
        status=IncidentStatus.RESOLVED,
        severity=Severity.HIGH,
        correlation_id="corr-e2e-001",
        dedup_fingerprint="fp-e2e-001",
        source="wazuh",
        raw_alert={},
        evidence={
            "summary": "Brute-force login detected",
            "verdict": "real",
            "flags": ["brute-force", "known-ip"],
        },
        disposition="auto_remediated",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _seed_summary():
    from backend.domain.dashboard import IncidentSummary

    return IncidentSummary(
        id=_INC_ID,
        status="resolved",
        severity="high",
        disposition="auto_remediated",
        source="wazuh",
        summary="Brute-force login detected",
        is_awaiting_approval=False,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _seed_audit_rows():
    from backend.repositories.audit import AuditRow

    return [
        AuditRow(
            id=1,
            incident_id=_INC_ID,
            actor="system",
            action="add_to_watchlist",
            target="10.0.0.5",
            outcome="applied",
            idempotency_key="idem-e2e-001",
            created_at=_NOW,
        ),
        AuditRow(
            id=2,
            incident_id=_INC_ID,
            actor="system",
            action="open_ticket",
            target="TICKET-001",
            outcome="applied",
            idempotency_key="idem-e2e-002",
            created_at=_NOW,
        ),
    ]


def _make_app():
    from backend.dependencies import (
        get_approval_repo,
        get_audit_repo,
        get_auth_service,
        get_incident_repo,
        get_trace_repo,
    )
    from backend.domain.dashboard import MemoryHit, VolumeBucket
    from backend.infra.config import load_settings
    from backend.infra.container import clear_registry
    from backend.main import create_app

    clear_registry()
    settings = load_settings()
    app = create_app(settings)
    clear_registry()

    auth_svc = _make_auth_service()
    app.dependency_overrides[get_auth_service] = lambda: auth_svc

    incident = _seed_incident()
    summary = _seed_summary()
    audit_rows = _seed_audit_rows()

    async def fake_incident_repo():
        repo = AsyncMock()
        repo.list_for_queue = AsyncMock(return_value=[summary])
        repo.count_for_queue = AsyncMock(return_value=1)
        repo.get = AsyncMock(return_value=incident)
        repo.kpi_volume_buckets = AsyncMock(return_value=[VolumeBucket(bucket=_NOW, count=1)])
        repo.kpi_disposition_counts = AsyncMock(return_value={"auto_remediated": 1})
        repo.kpi_mean_time_to_disposition_ms = AsyncMock(return_value=30_000)
        repo.kpi_enriched_and_hit_counts = AsyncMock(
            return_value=MemoryHit(enriched=1, hits=1, rate=1.0)
        )
        repo.kpi_status_counts = AsyncMock(
            return_value={"active": 0, "awaiting_approval": 0, "auto_resolved": 1, "escalated": 0}
        )
        yield repo

    async def fake_audit_repo():
        repo = AsyncMock()
        repo.list_for_incident = AsyncMock(return_value=audit_rows)
        yield repo

    async def fake_approval_repo():
        repo = AsyncMock()
        repo.get_pending_for_incident = AsyncMock(return_value=None)
        yield repo

    async def fake_trace_repo():
        repo = AsyncMock()
        repo.get_trace_tree = AsyncMock(return_value=None)
        yield repo

    app.dependency_overrides[get_incident_repo] = fake_incident_repo
    app.dependency_overrides[get_audit_repo] = fake_audit_repo
    app.dependency_overrides[get_approval_repo] = fake_approval_repo
    app.dependency_overrides[get_trace_repo] = fake_trace_repo

    return app


@pytest.mark.e2e
class TestDashboardE2E:
    def setup_method(self):
        self.app = _make_app()

    def _login(self, client: TestClient) -> str:
        resp = client.post(
            "/auth/login",
            json={"username": "operator", "password": _PASSWORD},
        )
        assert resp.status_code == 200, f"Login failed: {resp.text}"
        token = resp.json()["access_token"]
        assert token
        return token

    def test_full_flow_login_queue_detail_audit(self) -> None:
        """1. Login → 2. Queue → 3. Detail → 4. Audit (the US1 golden path)."""
        with TestClient(self.app, raise_server_exceptions=False) as client:
            # Step 1: Login
            token = self._login(client)
            headers = {"Authorization": f"Bearer {token}"}

            # Step 2: Queue
            resp = client.get("/incidents", headers=headers)
            assert resp.status_code == 200
            queue = resp.json()
            assert queue["total"] == 1
            assert len(queue["items"]) == 1
            item = queue["items"][0]
            assert item["id"] == str(_INC_ID)
            assert item["severity"] == "high"
            assert item["status"] == "resolved"
            assert item["summary"] == "Brute-force login detected"
            assert item["is_awaiting_approval"] is False

            # Step 3: Detail
            resp = client.get(f"/incidents/{_INC_ID}", headers=headers)
            assert resp.status_code == 200
            detail = resp.json()
            assert detail["id"] == str(_INC_ID)
            assert detail["correlation_id"] == "corr-e2e-001"
            assert detail["evidence"]["summary"] == "Brute-force login detected"
            assert detail["pending_approval"] is None

            # Step 4: Audit
            resp = client.get(f"/incidents/{_INC_ID}/audit", headers=headers)
            assert resp.status_code == 200
            audit = resp.json()["audit"]
            assert len(audit) == 2
            actions = {row["action"] for row in audit}
            assert "add_to_watchlist" in actions
            assert "open_ticket" in actions
            for row in audit:
                assert row["outcome"] == "applied"

    def test_unauthenticated_requests_rejected(self) -> None:
        """All protected dashboard endpoints return 401 without a token."""
        with TestClient(self.app, raise_server_exceptions=False) as client:
            assert client.get("/incidents").status_code == 401
            assert client.get(f"/incidents/{_INC_ID}").status_code == 401
            assert client.get(f"/incidents/{_INC_ID}/audit").status_code == 401

    def test_unknown_incident_returns_404(self) -> None:
        from backend.dependencies import get_incident_repo

        unknown_id = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")

        # Override the incident repo so get() returns None for any id → 404 path.
        async def empty_incident_repo():
            repo = AsyncMock()
            repo.get = AsyncMock(return_value=None)
            yield repo

        self.app.dependency_overrides[get_incident_repo] = empty_incident_repo
        try:
            with TestClient(self.app, raise_server_exceptions=False) as client:
                token = self._login(client)
                headers = {"Authorization": f"Bearer {token}"}

                detail = client.get(f"/incidents/{unknown_id}", headers=headers)
                assert detail.status_code == 404

                # The audit endpoint also checks incident existence first.
                audit = client.get(f"/incidents/{unknown_id}/audit", headers=headers)
                assert audit.status_code == 404
        finally:
            self.app.dependency_overrides.pop(get_incident_repo, None)

    def test_queue_filter_by_status_applied(self) -> None:
        with TestClient(self.app, raise_server_exceptions=False) as client:
            token = self._login(client)
            headers = {"Authorization": f"Bearer {token}"}
            resp = client.get(
                "/incidents?view=resolved&status=escalated",
                headers=headers,
            )
            assert resp.status_code == 200
            body = resp.json()
            assert "escalated" in body["applied_filters"]["status"]

    def test_wrong_password_returns_401(self) -> None:
        with TestClient(self.app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/auth/login",
                json={"username": "operator", "password": "wrongpass"},
            )
            assert resp.status_code == 401

    def test_trace_endpoint_returns_empty_when_no_spans(self) -> None:
        with TestClient(self.app, raise_server_exceptions=False) as client:
            token = self._login(client)
            headers = {"Authorization": f"Bearer {token}"}
            resp = client.get(f"/incidents/{_INC_ID}/trace", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["correlation_id"] == "corr-e2e-001"
        assert body["root"] is None
        assert body["telemetry"]["step_count"] == 0

    def test_kpis_endpoint_returns_snapshot(self) -> None:
        with TestClient(self.app, raise_server_exceptions=False) as client:
            token = self._login(client)
            headers = {"Authorization": f"Bearer {token}"}
            resp = client.get("/incidents/kpis", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "volume_over_time" in body
        assert body["memory_hit"]["enriched"] == 1
        assert body["mean_time_to_disposition_ms"] == 30_000


@pytest.mark.e2e
class TestApprovalE2E:
    """US2 approve/reject + already-decided guard (T038)."""

    def _make_approval_app(
        self,
        *,
        approval_status: str = "pending",
        resolve_result: bool = True,
        supervisor_disposition: str = "remediated",
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
        from backend.repositories.approvals import ApprovalRecord

        clear_registry()
        settings = load_settings()
        app = create_app(settings)
        clear_registry()

        auth_svc = _make_auth_service()
        app.dependency_overrides[get_auth_service] = lambda: auth_svc

        record = ApprovalRecord(
            id=1,
            incident_id=_INC_ID,
            plan_id="plan-001",
            pending_actions=[{"action_id": "isolate_host", "target": "srv-01"}],
            rationale="Host compromised.",
            status=approval_status,
            deadline_at=_NOW + timedelta(hours=1),
            decided_by=None,
            decided_at=None,
            created_at=_NOW,
            updated_at=_NOW,
        )

        from backend.domain.incident import Incident, IncidentStatus, Severity

        resolved_inc = Incident(
            id=_INC_ID,
            status=IncidentStatus.RESOLVED,
            severity=Severity.HIGH,
            correlation_id="corr-e2e-001",
            dedup_fingerprint="fp-e2e-001",
            source="wazuh",
            raw_alert={},
            disposition=supervisor_disposition,
            created_at=_NOW,
            updated_at=_NOW,
        )

        mock_supervisor = AsyncMock()
        mock_supervisor.resume_incident = AsyncMock(return_value=supervisor_disposition)

        async def fake_approval_repo():
            repo = AsyncMock()
            repo.get = AsyncMock(return_value=record)
            repo.resolve = AsyncMock(return_value=resolve_result)
            yield repo

        async def fake_audit_repo():
            repo = AsyncMock()
            repo.append = AsyncMock(return_value=True)
            yield repo

        async def fake_incident_repo():
            repo = AsyncMock()
            repo.get = AsyncMock(return_value=resolved_inc)
            repo.list_for_queue = AsyncMock(return_value=[])
            repo.count_for_queue = AsyncMock(return_value=0)
            yield repo

        app.dependency_overrides[get_approval_repo] = fake_approval_repo
        app.dependency_overrides[get_audit_repo] = fake_audit_repo
        app.dependency_overrides[get_incident_repo] = fake_incident_repo
        app.dependency_overrides[get_supervisor] = lambda: mock_supervisor

        return app

    def _login(self, client, password: str = _PASSWORD) -> str:
        resp = client.post("/auth/login", json={"username": "operator", "password": password})
        assert resp.status_code == 200
        return resp.json()["access_token"]

    def test_approve_returns_remediated(self) -> None:
        app = self._make_approval_app(supervisor_disposition="remediated")
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client)
            resp = client.post(
                "/approvals/1/decision",
                json={"decision": "approve"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["disposition"] == "remediated"
        assert body["decision"] == "approve"

    def test_reject_returns_rejected_by_human(self) -> None:
        app = self._make_approval_app(supervisor_disposition="rejected_by_human")
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client)
            resp = client.post(
                "/approvals/1/decision",
                json={"decision": "reject"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["decision"] == "reject"

    def test_already_decided_returns_409(self) -> None:
        app = self._make_approval_app(approval_status="approved")
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client)
            resp = client.post(
                "/approvals/1/decision",
                json={"decision": "approve"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 409

    def test_race_condition_returns_409(self) -> None:
        app = self._make_approval_app(resolve_result=False)
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client)
            resp = client.post(
                "/approvals/1/decision",
                json={"decision": "approve"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 409

    def test_unauthenticated_decision_returns_401(self) -> None:
        app = self._make_approval_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/approvals/1/decision", json={"decision": "approve"})
        assert resp.status_code == 401
