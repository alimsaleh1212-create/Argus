"""Integration tests — T024 [US4]: at-least-one-reachable readiness gate.

Verifies:
- /ready reports llm dependency healthy iff ≥1 provider reachable.
- /ready returns 503 only when NO provider is reachable.
- Boot is not crashed by provider unreachability.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.domain.health import DependencyStatus


def _make_app_with_health():
    from backend.infra.config import Settings
    from backend.infra.container import AppContainer
    from backend.routers.health import router

    app = FastAPI()
    app.state.settings = Settings()
    app.state.container = AppContainer()
    app.include_router(router)
    return app


class TestLlmReadinessGate:
    async def test_llm_healthy_when_gemini_reachable(self) -> None:
        """check_llm returns healthy when at least one provider responds (FR-019)."""
        from backend.infra.config import Settings
        from backend.infra.health import check_llm
        from backend.infra.llm_drivers import GeminiDriver, OllamaDriver

        with (
            patch.object(GeminiDriver, "ping", new=AsyncMock(return_value=True)),
            patch.object(OllamaDriver, "ping", new=AsyncMock(return_value=False)),
        ):
            settings = Settings()
            status = await check_llm(settings)

        assert status.healthy is True
        assert status.name == "llm"

    async def test_llm_unhealthy_when_neither_reachable(self) -> None:
        """check_llm returns unhealthy when no provider responds."""
        from backend.infra.config import Settings
        from backend.infra.health import check_llm
        from backend.infra.llm_drivers import GeminiDriver, OllamaDriver

        with (
            patch.object(GeminiDriver, "ping", new=AsyncMock(return_value=False)),
            patch.object(OllamaDriver, "ping", new=AsyncMock(return_value=False)),
        ):
            settings = Settings()
            status = await check_llm(settings)

        assert status.healthy is False

    async def test_llm_healthy_when_ollama_reachable_and_gemini_not(self) -> None:
        """At-least-one: Ollama reachable → healthy even if Gemini is down."""
        from backend.infra.config import Settings
        from backend.infra.health import check_llm
        from backend.infra.llm_drivers import GeminiDriver, OllamaDriver

        with (
            patch.object(GeminiDriver, "ping", new=AsyncMock(return_value=False)),
            patch.object(OllamaDriver, "ping", new=AsyncMock(return_value=True)),
        ):
            settings = Settings()
            status = await check_llm(settings)

        assert status.healthy is True

    def test_ready_503_when_llm_not_healthy(self) -> None:
        """/ready returns 503 when the llm dependency is not healthy (SC-010)."""
        from backend.domain.health import DependencyStatus

        with patch(
            "backend.routers.health.run_readiness_probes", new=AsyncMock(return_value=[
                DependencyStatus(name="vault", healthy=True),
                DependencyStatus(name="postgres", healthy=True),
                DependencyStatus(name="minio", healthy=True),
                DependencyStatus(name="llm", healthy=False, detail="No providers reachable"),
            ])
        ):
            app = _make_app_with_health()
            with TestClient(app) as client:
                resp = client.get("/ready")

        assert resp.status_code == 503
        body = resp.json()
        llm_dep = next(d for d in body["dependencies"] if d["name"] == "llm")
        assert llm_dep["healthy"] is False

    def test_ready_200_when_llm_healthy(self) -> None:
        """/ready returns 200 when llm dep is healthy (FR-019 / SC-010)."""
        from backend.domain.health import DependencyStatus

        with patch(
            "backend.routers.health.run_readiness_probes", new=AsyncMock(return_value=[
                DependencyStatus(name="vault", healthy=True),
                DependencyStatus(name="postgres", healthy=True),
                DependencyStatus(name="minio", healthy=True),
                DependencyStatus(name="llm", healthy=True),
            ])
        ):
            app = _make_app_with_health()
            with TestClient(app) as client:
                resp = client.get("/ready")

        assert resp.status_code == 200
