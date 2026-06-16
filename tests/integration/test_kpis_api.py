"""Integration tests for GET /incidents/kpis (T051)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

_JWT_SECRET = "kpi-jwt-secret-long-enough-32chars"
_NOW = datetime(2026, 6, 12, 9, 0, 0, tzinfo=UTC)


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

    salt = "kpi-test-salt"
    password = "kpi-pass"
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
    from backend.domain.dashboard import MemoryHit, VolumeBucket

    repo = AsyncMock()
    repo.kpi_volume_buckets = AsyncMock(
        return_value=[
            VolumeBucket(bucket=_NOW, count=12),
            VolumeBucket(bucket=_NOW, count=8),
        ]
    )
    repo.kpi_disposition_counts = AsyncMock(
        return_value={"auto_remediated": 15, "escalated": 3, "rejected_by_human": 1}
    )
    repo.kpi_mean_time_to_disposition_ms = AsyncMock(return_value=90_000)
    repo.kpi_enriched_and_hit_counts = AsyncMock(
        return_value=MemoryHit(enriched=20, hits=8, rate=0.4, bias_applied=3)
    )
    return repo


@pytest.mark.integration
class TestKpisEndpoint:
    def _login(self, client: TestClient, password: str) -> str:
        resp = client.post("/auth/login", json={"username": "operator", "password": password})
        assert resp.status_code == 200
        return resp.json()["access_token"]

    def test_kpis_returns_200_with_snapshot(self) -> None:
        app, pw = _make_app(mock_repo=_make_mock_repo())
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get("/incidents/kpis", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.json()
        assert "volume_over_time" in body
        assert "disposition_split" in body
        assert "memory_hit" in body
        assert "generated_at" in body

    def test_kpis_volume_buckets_shape(self) -> None:
        app, pw = _make_app(mock_repo=_make_mock_repo())
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get("/incidents/kpis", headers={"Authorization": f"Bearer {token}"})
        vol = resp.json()["volume_over_time"]
        assert len(vol) == 2
        assert vol[0]["count"] == 12

    def test_kpis_disposition_split_counts(self) -> None:
        app, pw = _make_app(mock_repo=_make_mock_repo())
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get("/incidents/kpis", headers={"Authorization": f"Bearer {token}"})
        split = resp.json()["disposition_split"]
        assert split["auto_remediated"] == 15
        assert split["escalated"] == 3

    def test_kpis_memory_hit_rate(self) -> None:
        app, pw = _make_app(mock_repo=_make_mock_repo())
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get("/incidents/kpis", headers={"Authorization": f"Bearer {token}"})
        mh = resp.json()["memory_hit"]
        assert mh["enriched"] == 20
        assert mh["hits"] == 8
        assert abs(mh["rate"] - 0.4) < 0.001
        assert mh["bias_applied"] == 3

    def test_kpis_mttd_ms(self) -> None:
        app, pw = _make_app(mock_repo=_make_mock_repo())
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get("/incidents/kpis", headers={"Authorization": f"Bearer {token}"})
        assert resp.json()["mean_time_to_disposition_ms"] == 90_000

    def test_kpis_unauthenticated_returns_401(self) -> None:
        app, _pw = _make_app(mock_repo=_make_mock_repo())
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/incidents/kpis")
        assert resp.status_code == 401

    def test_kpis_not_swallowed_by_incident_id_route(self) -> None:
        """Ensure /incidents/kpis is not matched by /{incident_id}."""
        app, pw = _make_app(mock_repo=_make_mock_repo())
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get("/incidents/kpis", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert "volume_over_time" in resp.json()

    def test_kpis_zero_enriched_rate_is_null(self) -> None:
        from backend.domain.dashboard import MemoryHit

        mock = AsyncMock()
        mock.kpi_volume_buckets = AsyncMock(return_value=[])
        mock.kpi_disposition_counts = AsyncMock(return_value={})
        mock.kpi_mean_time_to_disposition_ms = AsyncMock(return_value=None)
        mock.kpi_enriched_and_hit_counts = AsyncMock(
            return_value=MemoryHit(enriched=0, hits=0, rate=None, bias_applied=0)
        )
        app, pw = _make_app(mock_repo=mock)
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get("/incidents/kpis", headers={"Authorization": f"Bearer {token}"})
        mh = resp.json()["memory_hit"]
        assert mh["rate"] is None
        assert mh["bias_applied"] == 0

    def test_kpis_feedback_bias_count(self) -> None:
        from backend.domain.dashboard import MemoryHit

        mock = AsyncMock()
        mock.kpi_volume_buckets = AsyncMock(return_value=[])
        mock.kpi_disposition_counts = AsyncMock(return_value={})
        mock.kpi_mean_time_to_disposition_ms = AsyncMock(return_value=None)
        mock.kpi_enriched_and_hit_counts = AsyncMock(
            return_value=MemoryHit(enriched=10, hits=5, rate=0.5, bias_applied=7)
        )
        app, pw = _make_app(mock_repo=mock)
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get("/incidents/kpis", headers={"Authorization": f"Bearer {token}"})
        assert resp.json()["memory_hit"]["bias_applied"] == 7
