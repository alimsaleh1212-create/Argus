"""Integration tests for GET /incidents/{id}/trace (T041)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

_INC_ID = uuid.UUID("cccccccc-0000-0000-0000-000000000001")
_CORR_ID = "corr-trace-001"
_NOW = datetime(2026, 6, 12, 9, 0, 0, tzinfo=UTC)
_JWT_SECRET = "trace-jwt-secret-long-enough-32chars"


def _make_app(*, incident=None, trace_tree=None):
    """Create a test app with mocked incident and trace repos."""
    import hashlib

    from backend.dependencies import (
        get_auth_service,
        get_incident_repo,
        get_trace_repo,
    )
    from backend.infra.config import load_settings
    from backend.infra.container import clear_registry
    from backend.main import create_app
    from backend.services.auth import AuthService

    clear_registry()
    settings = load_settings()
    app = create_app(settings)
    clear_registry()

    salt = "trace-test-salt"
    password = "trace-pass"
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

    async def fake_incident_repo():
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=incident)
        yield repo

    async def fake_trace_repo():
        repo = AsyncMock()
        repo.get_trace_tree = AsyncMock(return_value=trace_tree)
        yield repo

    app.dependency_overrides[get_incident_repo] = fake_incident_repo
    app.dependency_overrides[get_trace_repo] = fake_trace_repo

    return app, password


def _seed_incident(*, correlation_id: str | None = _CORR_ID):
    from backend.domain.incident import Incident, IncidentStatus, Severity

    return Incident(
        id=_INC_ID,
        status=IncidentStatus.RESOLVED,
        severity=Severity.HIGH,
        correlation_id=correlation_id,
        dedup_fingerprint="fp-trace-001",
        source="wazuh",
        raw_alert={},
        disposition="auto_remediated",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _seed_trace_tree():
    from backend.domain.telemetry import Span, SpanKind, SpanStatus, TraceTree

    root = Span(
        span_id="root-span",
        trace_id="trace-001",
        correlation_id=_CORR_ID,
        name="incident_pipeline",
        kind=SpanKind.ROOT,
        started_at=_NOW,
        ended_at=_NOW + timedelta(milliseconds=800),
        status=SpanStatus.OK,
    )
    child = Span(
        span_id="triage-span",
        trace_id="trace-001",
        correlation_id=_CORR_ID,
        name="triage",
        kind=SpanKind.LLM_CALL,
        started_at=_NOW,
        ended_at=_NOW + timedelta(milliseconds=300),
        status=SpanStatus.OK,
        parent_span_id="root-span",
        tokens_in=50,
        tokens_out=120,
        llm_model="gemini-1.5-pro",
    )
    return TraceTree(root=root, children={"root-span": [child]})


@pytest.mark.integration
class TestTraceEndpoint:
    def _login(self, client: TestClient, password: str) -> str:
        resp = client.post("/auth/login", json={"username": "operator", "password": password})
        assert resp.status_code == 200
        return resp.json()["access_token"]

    def test_trace_returns_200_with_tree(self) -> None:
        app, pw = _make_app(incident=_seed_incident(), trace_tree=_seed_trace_tree())
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get(
                f"/incidents/{_INC_ID}/trace", headers={"Authorization": f"Bearer {token}"}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["correlation_id"] == _CORR_ID
        assert body["root"]["span_id"] == "root-span"
        assert body["root"]["kind"] == "root"
        assert body["root"]["status"] == "ok"

    def test_trace_children_serialized(self) -> None:
        app, pw = _make_app(incident=_seed_incident(), trace_tree=_seed_trace_tree())
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get(
                f"/incidents/{_INC_ID}/trace", headers={"Authorization": f"Bearer {token}"}
            )
        body = resp.json()
        children = body["children"]
        assert "root-span" in children
        assert len(children["root-span"]) == 1
        child = children["root-span"][0]
        assert child["span_id"] == "triage-span"
        assert child["llm_model"] == "gemini-1.5-pro"
        assert child["tokens_in"] == 50
        assert child["tokens_out"] == 120

    def test_trace_telemetry_rollup(self) -> None:
        app, pw = _make_app(incident=_seed_incident(), trace_tree=_seed_trace_tree())
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get(
                f"/incidents/{_INC_ID}/trace", headers={"Authorization": f"Bearer {token}"}
            )
        telemetry = resp.json()["telemetry"]
        assert telemetry["step_count"] == 1
        assert telemetry["error_steps"] == 0
        assert telemetry["total_tokens_in"] == 50
        assert telemetry["total_tokens_out"] == 120
        assert telemetry["end_to_end_ms"] == 800

    def test_no_spans_yet_returns_200_empty_tree(self) -> None:
        app, pw = _make_app(incident=_seed_incident(), trace_tree=None)
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get(
                f"/incidents/{_INC_ID}/trace", headers={"Authorization": f"Bearer {token}"}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["correlation_id"] == _CORR_ID
        assert body["root"] is None
        assert body["children"] == {}
        telemetry = body["telemetry"]
        assert telemetry["step_count"] == 0
        assert telemetry["error_steps"] == 0
        assert telemetry["total_tokens_in"] is None
        assert telemetry["total_tokens_out"] is None

    def test_unknown_incident_returns_404(self) -> None:
        app, pw = _make_app(incident=None, trace_tree=None)
        unknown = uuid.UUID("dddddddd-0000-0000-0000-000000000002")
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get(
                f"/incidents/{unknown}/trace", headers={"Authorization": f"Bearer {token}"}
            )
        assert resp.status_code == 404

    def test_unauthenticated_returns_401(self) -> None:
        app, _pw = _make_app(incident=_seed_incident(), trace_tree=_seed_trace_tree())
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get(f"/incidents/{_INC_ID}/trace")
        assert resp.status_code == 401

    def test_null_tokens_serialized_as_null_not_zero(self) -> None:
        from backend.domain.telemetry import Span, SpanKind, SpanStatus, TraceTree

        root = Span(
            span_id="root-span",
            trace_id="trace-001",
            correlation_id=_CORR_ID,
            name="pipeline",
            kind=SpanKind.ROOT,
            started_at=_NOW,
            ended_at=_NOW + timedelta(milliseconds=100),
            status=SpanStatus.OK,
        )
        tree = TraceTree(root=root, children={})
        app, pw = _make_app(incident=_seed_incident(), trace_tree=tree)
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get(
                f"/incidents/{_INC_ID}/trace", headers={"Authorization": f"Bearer {token}"}
            )
        body = resp.json()
        assert body["root"]["tokens_in"] is None
        assert body["root"]["tokens_out"] is None
        assert body["telemetry"]["total_tokens_in"] is None
