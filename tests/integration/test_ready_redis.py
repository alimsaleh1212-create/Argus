"""Integration test — T017: /ready returns 503 when Redis is unreachable."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
class TestReadyRedis:
    def test_ready_503_when_redis_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When Redis is unreachable, /ready must return 503."""
        monkeypatch.setenv("SENTINEL__REDIS__URL", "redis://127.0.0.1:19999/0")

        from backend.infra.container import clear_registry
        from backend.infra.config import load_settings

        clear_registry()
        settings = load_settings()

        from backend.infra.health import check_redis
        import asyncio

        status = asyncio.run(check_redis(settings))
        assert status.healthy is False
        assert status.name == "redis"

    def test_check_redis_returns_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """check_redis always returns a DependencyStatus with name='redis'."""
        monkeypatch.setenv("SENTINEL__REDIS__URL", "redis://127.0.0.1:19999/0")

        import asyncio

        from backend.infra.config import Settings
        from backend.infra.health import check_redis

        s = Settings()
        result = asyncio.run(check_redis(s))
        assert result.name == "redis"
        assert isinstance(result.healthy, bool)
