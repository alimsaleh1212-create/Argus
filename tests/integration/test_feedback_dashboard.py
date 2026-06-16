"""Integration tests for feedback dashboard surface (US3, T020).

Mocks the incident repository so no Postgres connection is needed.
Auth uses the same pattern as test_incidents_api.py / test_kpis_api.py.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from backend.domain.dashboard import MemoryHit, VolumeBucket

_SALT = "fdbsalt"
_ITERATIONS = 1000
_PASSWORD = "dashboardpass"
_HASH = hashlib.pbkdf2_hmac("sha256", _PASSWORD.encode(), _SALT.encode(), _ITERATIONS).hex()
_JWT_SECRET = "fdb-jwt-secret-long-enough-32chars"
_NOW = datetime(2026, 6, 12, 9, 0, 0, tzinfo=UTC)


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


def _make_app(*, mock_repo=None, incident=None):
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

    if mock_repo is not None:

        async def fake_incident_repo():
            yield mock_repo

        app.dependency_overrides[get_incident_repo] = fake_incident_repo

    async def fake_audit_repo():
        repo = AsyncMock()
        repo.list_for_incident = AsyncMock(return_value=[])
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

    app.dependency_overrides[get_audit_repo] = fake_audit_repo
    app.dependency_overrides[get_approval_repo] = fake_approval_repo
    app.dependency_overrides[get_redactor_dep] = lambda: _FakeRedactor()

    return app


def _make_mock_repo(*, bias_applied: int = 0):
    repo = AsyncMock()
    repo.kpi_volume_buckets = AsyncMock(return_value=[VolumeBucket(bucket=_NOW, count=1)])
    repo.kpi_disposition_counts = AsyncMock(return_value={})
    repo.kpi_mean_time_to_disposition_ms = AsyncMock(return_value=None)
    repo.kpi_enriched_and_hit_counts = AsyncMock(
        return_value=MemoryHit(enriched=10, hits=5, rate=0.5, bias_applied=bias_applied)
    )
    return repo


def _get_token(app) -> str:
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/auth/login", json={"username": "admin", "password": _PASSWORD})
    return resp.json()["access_token"]


@pytest.mark.integration
class TestFeedbackKpi:
    def test_kpi_includes_feedback_bias_count(self) -> None:
        app = _make_app(mock_repo=_make_mock_repo(bias_applied=4))
        token = _get_token(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/incidents/kpis", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["memory_hit"]["bias_applied"] == 4

    def test_kpi_feedback_bias_zero_when_none(self) -> None:
        app = _make_app(mock_repo=_make_mock_repo(bias_applied=0))
        token = _get_token(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/incidents/kpis", headers={"Authorization": f"Bearer {token}"})
        assert resp.json()["memory_hit"]["bias_applied"] == 0


@pytest.mark.integration
class TestFeedbackTrace:
    def test_incident_detail_exposes_prior_outcome(self) -> None:
        from backend.domain.incident import Incident, IncidentStatus, Severity

        inc_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        fake_inc = Incident(
            id=inc_id,
            status=IncidentStatus.TRIAGING,
            severity=Severity.HIGH,
            correlation_id="corr-trace",
            dedup_fingerprint="fp-trace",
            source="wazuh",
            raw_alert={},
            evidence={
                "summary": "Repeat indicator",
                "prior_outcome": {
                    "signals": [
                        {
                            "indicator": "10.0.0.1",
                            "outcome": "regressed",
                            "is_current": True,
                        }
                    ],
                    "biased_severity": "critical",
                },
            },
            created_at=_NOW,
            updated_at=_NOW,
        )
        repo = _make_mock_repo()
        repo.get = AsyncMock(return_value=fake_inc)
        app = _make_app(mock_repo=repo)
        token = _get_token(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get(
                f"/incidents/{inc_id}", headers={"Authorization": f"Bearer {token}"}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["evidence"]["prior_outcome"]["biased_severity"] == "critical"
        assert body["evidence"]["prior_outcome"]["signals"][0]["outcome"] == "regressed"
