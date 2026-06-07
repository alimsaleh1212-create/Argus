"""Unit tests — T029: dependency_overrides and duplicate-name fast-fail."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class TestDependencyOverrides:
    def test_override_replaces_provider(self) -> None:
        """app.dependency_overrides substitutes a provider without changing consumer code."""
        from fastapi import Depends

        app = FastAPI()

        async def real_dep() -> str:
            return "real"

        async def fake_dep() -> str:
            return "fake"

        @app.get("/test")
        async def endpoint(value: str = Depends(real_dep)) -> dict:
            return {"value": value}

        app.dependency_overrides[real_dep] = fake_dep

        with TestClient(app) as client:
            response = client.get("/test")
            assert response.status_code == 200
            assert response.json() == {"value": "fake"}

    def test_duplicate_name_fails_at_registration(self) -> None:
        """Duplicate provider name must fail at register_provider() call."""
        from backend.infra.container import clear_registry, register_provider

        clear_registry()

        class P:
            name = "same_name"

            def build(self, settings):
                pass

        register_provider(P())
        with pytest.raises(ValueError, match="already registered"):
            register_provider(P())

        clear_registry()
