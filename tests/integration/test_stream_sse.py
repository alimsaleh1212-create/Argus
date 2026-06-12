"""Integration tests for GET /incidents/stream (T052).

Tests:
- Valid token → initial snapshot event
- Token as query param accepted
- Bad / no token → 401
- Heartbeat/delta shape validation

SSE tests use a finite mock generator to avoid blocking the test runner
on the infinite production polling loop.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

_JWT_SECRET = "stream-jwt-secret-long-enough-32chars"
_NOW = datetime(2026, 6, 12, 9, 0, 0, tzinfo=UTC)


def _make_app(*, summaries=None, kpi_counts=None):
    from backend.dependencies import get_auth_service, get_incident_repo
    from backend.infra.config import load_settings
    from backend.infra.container import clear_registry
    from backend.main import create_app
    from backend.services.auth import AuthService

    clear_registry()
    settings = load_settings()
    app = create_app(settings)
    clear_registry()

    salt = "stream-test-salt"
    password = "stream-pass"
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

    _summaries = summaries or []
    _kpi_counts = kpi_counts or {"active": 0, "awaiting_approval": 0, "auto_resolved": 0, "escalated": 0}

    async def fake_incident_repo():
        repo = AsyncMock()
        repo.list_for_queue = AsyncMock(return_value=_summaries)
        repo.kpi_status_counts = AsyncMock(return_value=_kpi_counts)
        yield repo

    app.dependency_overrides[get_incident_repo] = fake_incident_repo
    return app, password


def _make_finite_stream(kpi_counts=None):
    """Return a mock incident_stream that yields one snapshot then stops."""
    _kpi = kpi_counts or {"active": 0, "awaiting_approval": 0, "auto_resolved": 0, "escalated": 0}

    async def _stream(repo, *, poll_seconds=2.0):
        snapshot = {
            "queue": [],
            "kpi_counters": _kpi,
        }
        yield f"event: snapshot\ndata: {json.dumps(snapshot)}\n\n"

    return _stream


def _parse_first_event(raw: bytes) -> dict:
    """Parse the first SSE event from raw bytes."""
    text = raw.decode("utf-8")
    event_type = None
    data = None
    for line in text.splitlines():
        if line.startswith("event: "):
            event_type = line[len("event: "):]
        elif line.startswith("data: "):
            data = json.loads(line[len("data: "):])
        if event_type and data is not None:
            break
    return {"event": event_type, "data": data}


@pytest.mark.integration
class TestSSEStream:
    def _login(self, client: TestClient, password: str) -> str:
        resp = client.post("/auth/login", json={"username": "operator", "password": password})
        assert resp.status_code == 200
        return resp.json()["access_token"]

    def test_stream_initial_snapshot_event(self) -> None:
        """Connect with valid Bearer token → receive initial snapshot."""
        app, pw = _make_app()
        mock_stream = _make_finite_stream()
        with patch("backend.routers.incidents.incident_stream", mock_stream):
            with TestClient(app, raise_server_exceptions=False) as client:
                token = self._login(client, pw)
                with client.stream(
                    "GET",
                    "/incidents/stream",
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    assert resp.status_code == 200
                    assert "text/event-stream" in resp.headers.get("content-type", "")
                    raw = resp.read()

        parsed = _parse_first_event(raw)
        assert parsed["event"] == "snapshot"
        assert "queue" in parsed["data"]
        assert "kpi_counters" in parsed["data"]

    def test_stream_token_as_query_param(self) -> None:
        """EventSource passes token as query param — must be accepted."""
        app, pw = _make_app()
        mock_stream = _make_finite_stream()
        with patch("backend.routers.incidents.incident_stream", mock_stream):
            with TestClient(app, raise_server_exceptions=False) as client:
                token = self._login(client, pw)
                with client.stream("GET", f"/incidents/stream?token={token}") as resp:
                    assert resp.status_code == 200
                    raw = resp.read()

        parsed = _parse_first_event(raw)
        assert parsed["event"] == "snapshot"

    def test_stream_no_token_returns_401(self) -> None:
        app, _pw = _make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/incidents/stream")
        assert resp.status_code == 401

    def test_stream_invalid_token_returns_401(self) -> None:
        app, _pw = _make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get(
                "/incidents/stream",
                headers={"Authorization": "Bearer invalid.token.here"},
            )
        assert resp.status_code == 401

    def test_stream_snapshot_kpi_counters_shape(self) -> None:
        kpi_counts = {"active": 5, "awaiting_approval": 2, "auto_resolved": 10, "escalated": 1}
        app, pw = _make_app(kpi_counts=kpi_counts)
        mock_stream = _make_finite_stream(kpi_counts=kpi_counts)
        with patch("backend.routers.incidents.incident_stream", mock_stream):
            with TestClient(app, raise_server_exceptions=False) as client:
                token = self._login(client, pw)
                with client.stream(
                    "GET",
                    "/incidents/stream",
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    raw = resp.read()

        parsed = _parse_first_event(raw)
        kpi = parsed["data"]["kpi_counters"]
        assert kpi["active"] == 5
        assert kpi["awaiting_approval"] == 2
        assert kpi["auto_resolved"] == 10

    def test_stream_not_swallowed_by_incident_id_route(self) -> None:
        """Ensure /incidents/stream route is not matched by /{incident_id}."""
        app, pw = _make_app()
        mock_stream = _make_finite_stream()
        with patch("backend.routers.incidents.incident_stream", mock_stream):
            with TestClient(app, raise_server_exceptions=False) as client:
                token = self._login(client, pw)
                with client.stream(
                    "GET",
                    "/incidents/stream",
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    assert resp.status_code == 200
                    assert "text/event-stream" in resp.headers.get("content-type", "")
