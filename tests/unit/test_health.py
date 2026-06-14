"""Unit tests — T032: /health and /ready endpoints.

/health: liveness — always 200 {"status":"ok"}, zero dependency I/O.
/ready: readiness — 200 all-healthy, 503 when any dep is unhealthy.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_test_app() -> FastAPI:
    """Build a minimal test app with health router attached."""
    from backend.infra.config import Settings
    from backend.infra.container import AppContainer
    from backend.routers.health import router

    app = FastAPI()
    app.state.settings = Settings()
    app.state.container = AppContainer()
    app.include_router(router)
    return app


class TestLivenessEndpoint:
    def test_health_returns_200_ok(self) -> None:
        """/health always returns 200 with status=ok."""
        app = _make_test_app()
        with TestClient(app) as client:
            resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_health_performs_zero_io(self) -> None:
        """/health must not call any database/vault/minio probe."""
        app = _make_test_app()
        with patch("backend.infra.health.check_vault", side_effect=AssertionError("IO called")):
            with TestClient(app) as client:
                resp = client.get("/health")
        assert resp.status_code == 200


class TestReadinessEndpoint:
    def test_ready_returns_200_when_all_healthy(self) -> None:
        """/ready returns 200 when all dependencies are healthy."""
        from backend.domain.health import DependencyStatus

        with patch(
            "backend.routers.health.run_readiness_probes", new_callable=AsyncMock
        ) as mock_probes:
            mock_probes.return_value = [
                DependencyStatus(name="vault", healthy=True),
                DependencyStatus(name="postgres", healthy=True),
                DependencyStatus(name="minio", healthy=True),
            ]
            app = _make_test_app()
            with TestClient(app) as client:
                resp = client.get("/ready")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ready"] is True

    def test_ready_returns_503_when_dep_unhealthy(self) -> None:
        """/ready returns 503 with healthy=false for the failing dep."""
        from backend.domain.health import DependencyStatus

        with patch(
            "backend.routers.health.run_readiness_probes", new_callable=AsyncMock
        ) as mock_probes:
            mock_probes.return_value = [
                DependencyStatus(name="vault", healthy=False, detail="connection refused"),
                DependencyStatus(name="postgres", healthy=True),
                DependencyStatus(name="minio", healthy=True),
            ]
            app = _make_test_app()
            with TestClient(app) as client:
                resp = client.get("/ready")

        assert resp.status_code == 503
        body = resp.json()
        assert body["ready"] is False
        vault_status = next(d for d in body["dependencies"] if d["name"] == "vault")
        assert vault_status["healthy"] is False


class TestPostgresProbe:
    """Regression: the readiness probe must hand asyncpg a libpq DSN.

    Settings stores a SQLAlchemy DSN ("postgresql+asyncpg://…"); raw
    asyncpg.connect() rejects the "+asyncpg" dialect with ClientConfigurationError,
    which silently made /ready return 503 forever (the compose smoke job's failure).
    """

    async def test_strips_sqlalchemy_dialect_before_asyncpg(self) -> None:
        from types import SimpleNamespace

        from pydantic import SecretStr

        from backend.infra.health import check_postgres

        captured: dict[str, str] = {}

        async def fake_connect(dsn: str):
            captured["dsn"] = dsn

            class _Conn:
                async def close(self) -> None: ...

            return _Conn()

        settings = SimpleNamespace(
            startup=SimpleNamespace(dependency_timeout_s=5.0),
            postgres=SimpleNamespace(dsn=SecretStr("postgresql+asyncpg://u:p@h:5432/db")),
        )
        with patch("asyncpg.connect", side_effect=fake_connect):
            result = await check_postgres(settings)

        assert result.healthy is True
        assert captured["dsn"] == "postgresql://u:p@h:5432/db"
        assert "+asyncpg" not in captured["dsn"]
