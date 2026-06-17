"""Integration tests for GET /incidents/pipeline (SOC pipeline-map, M-a)."""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

_JWT_SECRET = "pipeline-jwt-secret-long-enough-32chars"


def _make_app(*, mock_repo=None):
    from backend.dependencies import get_auth_service, get_incident_repo
    from backend.infra.config import load_settings
    from backend.infra.container import clear_registry
    from backend.main import create_app
    from backend.services.auth import AuthService

    clear_registry()
    settings = load_settings()
    app = create_app(settings)
    clear_registry()

    salt = "pipeline-test-salt"
    password = "pipeline-pass"
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

    if mock_repo is not None:

        async def fake_incident_repo():
            yield mock_repo

        app.dependency_overrides[get_incident_repo] = fake_incident_repo

    return app, password


def _make_mock_repo():
    repo = AsyncMock()
    repo.status_counts = AsyncMock(
        return_value={"triaging": 4, "responding": 2, "awaiting_approval": 1}
    )
    repo.disposition_counts_since = AsyncMock(
        return_value={"auto_resolved_triage": 3, "escalated_response": 1, "auto_remediated": 5}
    )
    return repo


@pytest.mark.integration
class TestPipelineEndpoint:
    def _login(self, client: TestClient, password: str) -> str:
        resp = client.post("/auth/login", json={"username": "operator", "password": password})
        assert resp.status_code == 200
        return resp.json()["access_token"]

    def test_pipeline_returns_200_with_snapshot(self) -> None:
        app, pw = _make_app(mock_repo=_make_mock_repo())
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get("/incidents/pipeline", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.json()
        assert [s["key"] for s in body["stages"]] == ["intake", "triage", "enrichment", "response"]
        assert "terminals" in body
        assert "window_hours" in body

    def test_pipeline_stage_in_flight_and_branches(self) -> None:
        app, pw = _make_app(mock_repo=_make_mock_repo())
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get("/incidents/pipeline", headers={"Authorization": f"Bearer {token}"})
        body = resp.json()
        triage = next(s for s in body["stages"] if s["key"] == "triage")
        assert triage["in_flight"] == 4
        assert {b["to"]: b["count"] for b in triage["branches"]} == {"resolved": 3}
        assert body["terminals"]["awaiting"] == 1
        assert body["terminals"]["resolved"] == 8  # auto_resolved_triage(3) + auto_remediated(5)
        assert body["terminals"]["escalated"] == 1

    def test_pipeline_unauthenticated_returns_401(self) -> None:
        app, _pw = _make_app(mock_repo=_make_mock_repo())
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/incidents/pipeline")
        assert resp.status_code == 401

    def test_pipeline_not_swallowed_by_incident_id_route(self) -> None:
        app, pw = _make_app(mock_repo=_make_mock_repo())
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get("/incidents/pipeline", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert "stages" in resp.json()
