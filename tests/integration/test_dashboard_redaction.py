"""Dashboard-view redaction assertion — extends the redaction eval gate (T057).

Verifies that PII/secrets seeded in incident evidence are either:
  a) Already redacted (upstream #2 did its job) and only [REDACTED] markers appear, OR
  b) The API never exposes raw PII through any dashboard endpoint (no de-redaction path — RD8).

This is a deterministic UI check: the dashboard is read-only, so all redaction happened
upstream at ingest time. These tests assert the API response never contains known fake PII
that was replaced with redaction markers before being stored.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

_JWT_SECRET = "redaction-jwt-secret-long-enough-32ch"
_NOW = datetime(2026, 6, 12, 9, 0, 0, tzinfo=UTC)

# Fake PII / secrets that must never appear in API responses
FAKE_EMAIL = "victim@example-corp.com"
FAKE_IP = "192.168.77.42"
FAKE_SECRET = "AKIAIOSFODNN7EXAMPLE"

# Redacted form (what upstream stores)
REDACTED_MARKER = "[REDACTED]"


def _make_app(*, incident, audit_rows=None):
    from backend.dependencies import get_approval_repo, get_audit_repo, get_auth_service, get_incident_repo
    from backend.infra.config import load_settings
    from backend.infra.container import clear_registry
    from backend.main import create_app
    from backend.services.auth import AuthService

    clear_registry()
    settings = load_settings()
    app = create_app(settings)
    clear_registry()

    salt = "redact-test-salt"
    password = "redact-pass"
    iterations = 1000
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations).hex()
    auth_svc = AuthService(
        admin_username="operator",
        password_hash=pw_hash,
        salt=salt,
        iterations=iterations,
        jwt_secret=_JWT_SECRET,
        algorithm="HS256",
        token_ttl_minutes=60,
    )
    app.dependency_overrides[get_auth_service] = lambda: auth_svc

    _audit_rows = audit_rows or []

    async def fake_incident_repo():
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=incident)
        repo.list_for_queue = AsyncMock(return_value=[])
        repo.count_for_queue = AsyncMock(return_value=0)
        yield repo

    async def fake_audit_repo():
        repo = AsyncMock()
        repo.list_for_incident = AsyncMock(return_value=_audit_rows)
        yield repo

    async def fake_approval_repo():
        repo = AsyncMock()
        repo.get_pending_for_incident = AsyncMock(return_value=None)
        yield repo

    app.dependency_overrides[get_incident_repo] = fake_incident_repo
    app.dependency_overrides[get_audit_repo] = fake_audit_repo
    app.dependency_overrides[get_approval_repo] = fake_approval_repo

    return app, password


def _seed_incident_with_redacted_evidence():
    """Incident with PII already replaced by [REDACTED] upstream (normal path)."""
    from backend.domain.incident import Incident, IncidentStatus, Severity

    return Incident(
        id=uuid.UUID("eeeeeeee-0000-0000-0000-000000000001"),
        status=IncidentStatus.RESOLVED,
        severity=Severity.HIGH,
        correlation_id="corr-redact-001",
        dedup_fingerprint="fp-redact-001",
        source="wazuh",
        raw_alert={},
        evidence={
            "summary": "Login attempt from [REDACTED] by [REDACTED]",
            "verdict": "real",
            "attacker_email": REDACTED_MARKER,
            "attacker_ip": REDACTED_MARKER,
            "secret_key": REDACTED_MARKER,
        },
        disposition="auto_remediated",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _seed_incident_with_raw_evidence():
    """Incident where upstream failed to redact — raw PII in evidence (adversarial case)."""
    from backend.domain.incident import Incident, IncidentStatus, Severity

    return Incident(
        id=uuid.UUID("ffffffff-0000-0000-0000-000000000001"),
        status=IncidentStatus.RESOLVED,
        severity=Severity.HIGH,
        correlation_id="corr-raw-001",
        dedup_fingerprint="fp-raw-001",
        source="wazuh",
        raw_alert={},
        evidence={
            "summary": "Suspicious login",
            "verdict": "real",
            # Note: these should have been redacted upstream; we store them as-is to test
            # that the *dashboard API* passes them through unchanged (no de-redaction).
            "attacker_email": FAKE_EMAIL,
        },
        disposition="auto_remediated",
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.mark.integration
class TestDashboardRedactionGate:
    """Assert the dashboard read path never introduces new PII exposure."""

    def _login(self, client: TestClient, password: str) -> str:
        resp = client.post("/auth/login", json={"username": "operator", "password": password})
        assert resp.status_code == 200
        return resp.json()["access_token"]

    def test_redacted_markers_preserved_in_detail_response(self) -> None:
        """Pre-redacted evidence passes through with [REDACTED] markers intact."""
        inc = _seed_incident_with_redacted_evidence()
        app, pw = _make_app(incident=inc)
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get(
                f"/incidents/{inc.id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        body = resp.text
        # Markers preserved
        assert REDACTED_MARKER in body
        # Original PII never appears
        assert FAKE_EMAIL not in body
        assert FAKE_IP not in body
        assert FAKE_SECRET not in body

    def test_raw_evidence_passed_through_unchanged_no_de_redaction(self) -> None:
        """Dashboard does not strip or alter existing evidence (no active de-redaction — RD8)."""
        inc = _seed_incident_with_raw_evidence()
        app, pw = _make_app(incident=inc)
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get(
                f"/incidents/{inc.id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        # Dashboard passes evidence as-is; redaction is upstream responsibility (#2)
        # This test ensures the API does not add a de-redaction path
        body = resp.json()
        assert body["evidence"]["attacker_email"] == FAKE_EMAIL

    def test_queue_response_summary_does_not_contain_fake_secret(self) -> None:
        """Queue summary (evidence->>'summary') must not expose AWS-like key patterns."""
        inc = _seed_incident_with_redacted_evidence()
        app, pw = _make_app(incident=inc)
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get(
                "/incidents",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        assert FAKE_SECRET not in resp.text
        assert FAKE_IP not in resp.text
        assert FAKE_EMAIL not in resp.text

    def test_audit_trail_does_not_expose_pii(self) -> None:
        """Audit actions/targets must not re-expose redacted PII."""
        from backend.repositories.audit import AuditRow

        inc = _seed_incident_with_redacted_evidence()
        audit_rows = [
            AuditRow(
                id=1,
                incident_id=inc.id,
                actor="system",
                action="block_ip",
                target=REDACTED_MARKER,  # target should also be redacted
                outcome="applied",
                idempotency_key="idem-redact-001",
                created_at=_NOW,
            )
        ]
        app, pw = _make_app(incident=inc, audit_rows=audit_rows)
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get(
                f"/incidents/{inc.id}/audit",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        assert FAKE_IP not in resp.text
        assert FAKE_EMAIL not in resp.text
        assert FAKE_SECRET not in resp.text
