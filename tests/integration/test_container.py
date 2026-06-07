"""Integration tests — T027: each registered provider builds once and
is reachable as container.<name>.
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
class TestContainerBuildOnce:
    def test_providers_build_and_are_reachable(self) -> None:
        """Providers build exactly once and are accessible on the container."""
        import asyncio
        from contextlib import asynccontextmanager
        from typing import Any

        from backend.infra.config import Settings
        from backend.infra.container import (
            AppContainer,
            clear_registry,
            get_registry,
            register_provider,
        )

        clear_registry()

        build_count = 0

        class CountingProvider:
            name = "test_resource"

            @asynccontextmanager
            async def build(self, settings: Any):
                nonlocal build_count
                build_count += 1
                yield "resource_value"

        register_provider(CountingProvider())

        settings = Settings()
        container = AppContainer()

        async def run():
            from contextlib import AsyncExitStack

            async with AsyncExitStack() as stack:
                for p in get_registry():
                    resource = await stack.enter_async_context(p.build(settings))
                    setattr(container, p.name, resource)

        asyncio.run(run())

        assert build_count == 1
        assert container.test_resource == "resource_value"  # type: ignore[attr-defined]

        clear_registry()

    def test_duplicate_name_registration_fails(self) -> None:
        """Registering two providers with the same name must fail at startup."""
        from backend.infra.container import clear_registry, register_provider

        clear_registry()

        class FakeProvider:
            name = "duplicate"

            def build(self, settings):
                pass

        register_provider(FakeProvider())

        with pytest.raises(ValueError, match="already registered"):
            register_provider(FakeProvider())

        clear_registry()
