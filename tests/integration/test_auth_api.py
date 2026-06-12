"""Integration tests: POST /auth/login + protected endpoint auth gate (#12)."""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

_SALT = "intsalt"
_ITERATIONS = 1000
_PASSWORD = "integrationpass"
_HASH = hashlib.pbkdf2_hmac("sha256", _PASSWORD.encode(), _SALT.encode(), _ITERATIONS).hex()
_JWT_SECRET = "int-jwt-secret"


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


def _make_app():
    """Build a minimal app with dependency_overrides for auth + no-lifespan providers."""
    from backend.infra.container import clear_registry
    from backend.infra.config import load_settings
    from backend.main import create_app
    from backend.dependencies import get_auth_service, get_current_operator, get_incident_repo

    # Clear all providers so lifespan doesn't try to connect to Vault/Postgres
    clear_registry()
    settings = load_settings()
    app = create_app(settings)
    # clear_registry again after _bootstrap_providers ran inside create_app
    clear_registry()

    auth_service = _make_auth_service()

    # Override the provider dependencies directly
    app.dependency_overrides[get_auth_service] = lambda: auth_service

    # Override get_incident_repo to avoid DB
    from unittest.mock import AsyncMock, MagicMock

    async def fake_incident_repo():
        repo = AsyncMock()
        repo.__aenter__ = AsyncMock(return_value=repo)
        repo.__aexit__ = AsyncMock(return_value=None)
        yield repo

    app.dependency_overrides[get_incident_repo] = fake_incident_repo

    return app, auth_service


@pytest.mark.integration
class TestAuthLogin:
    def test_valid_credentials_returns_token(self) -> None:
        app, _ = _make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/auth/login", json={"username": "admin", "password": _PASSWORD})
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"
        assert body["role"] == "admin"
        assert body["expires_in"] == 60 * 60

    def test_wrong_password_returns_401(self) -> None:
        app, _ = _make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/auth/login", json={"username": "admin", "password": "wrongpassword"})
        assert resp.status_code == 401
        assert "access_token" not in resp.json()

    def test_wrong_username_returns_401(self) -> None:
        app, _ = _make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/auth/login", json={"username": "hacker", "password": _PASSWORD})
        assert resp.status_code == 401

    def test_malformed_body_returns_422(self) -> None:
        app, _ = _make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/auth/login", json={"no_username": True})
        assert resp.status_code == 422


@pytest.mark.integration
class TestProtectedEndpoint:
    def _get_token(self, app) -> str:
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/auth/login", json={"username": "admin", "password": _PASSWORD})
        return resp.json()["access_token"]

    def test_no_token_returns_401(self) -> None:
        # /approvals has actual routes and is auth-guarded (router-level dependency)
        app, _ = _make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/approvals")
        assert resp.status_code == 401

    def test_valid_token_returns_not_401(self) -> None:
        app, _ = _make_app()
        token = self._get_token(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/approvals", headers={"Authorization": f"Bearer {token}"})
        # Not 401 — may be 200/500 depending on mock DB, but auth gate passed
        assert resp.status_code != 401

    def test_expired_token_returns_401(self) -> None:
        import jwt as pyjwt
        from datetime import UTC, datetime, timedelta

        payload = {
            "sub": "admin",
            "role": "admin",
            "iat": int((datetime.now(UTC) - timedelta(hours=2)).timestamp()),
            "exp": int((datetime.now(UTC) - timedelta(hours=1)).timestamp()),
        }
        expired_token = pyjwt.encode(payload, _JWT_SECRET, algorithm="HS256")

        app, _ = _make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/approvals", headers={"Authorization": f"Bearer {expired_token}"})
        assert resp.status_code == 401
