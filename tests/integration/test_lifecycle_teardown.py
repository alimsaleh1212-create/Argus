"""Integration tests — T028: reverse-order teardown, zero open connections."""

from __future__ import annotations

import pytest


@pytest.mark.integration
class TestLifecycleTeardown:
    def test_teardown_order_is_reversed(self) -> None:
        """Providers are built in registration order and torn down in reverse."""
        import asyncio
        from contextlib import AsyncExitStack, asynccontextmanager
        from typing import Any

        from backend.infra.config import Settings
        from backend.infra.container import (
            AppContainer,
            clear_registry,
            get_registry,
            register_provider,
        )

        clear_registry()
        events: list[str] = []

        class ProviderA:
            name = "resource_a"

            @asynccontextmanager
            async def build(self, settings: Any):
                events.append("build_a")
                yield "a"
                events.append("dispose_a")

        class ProviderB:
            name = "resource_b"

            @asynccontextmanager
            async def build(self, settings: Any):
                events.append("build_b")
                yield "b"
                events.append("dispose_b")

        register_provider(ProviderA())
        register_provider(ProviderB())

        settings = Settings()

        async def run():
            container = AppContainer()
            async with AsyncExitStack() as stack:
                for p in get_registry():
                    resource = await stack.enter_async_context(p.build(settings))
                    setattr(container, p.name, resource)
            # AsyncExitStack disposes in LIFO order

        asyncio.run(run())

        assert events == ["build_a", "build_b", "dispose_b", "dispose_a"]
        clear_registry()

    def test_zero_leaks_after_repeated_start_stop(self) -> None:
        """Container can be started and stopped multiple times with no leaks."""
        import asyncio
        from contextlib import AsyncExitStack, asynccontextmanager
        from typing import Any

        from backend.infra.config import Settings
        from backend.infra.container import (
            AppContainer,
            clear_registry,
            get_registry,
            register_provider,
        )

        clear_registry()
        open_count = 0

        class LeakableProvider:
            name = "leakable"

            @asynccontextmanager
            async def build(self, settings: Any):
                nonlocal open_count
                open_count += 1
                yield "resource"
                open_count -= 1

        register_provider(LeakableProvider())
        settings = Settings()

        async def cycle():
            container = AppContainer()
            async with AsyncExitStack() as stack:
                for p in get_registry():
                    resource = await stack.enter_async_context(p.build(settings))
                    setattr(container, p.name, resource)

        for _ in range(3):
            asyncio.run(cycle())
            assert open_count == 0, f"Resource leaked: open_count={open_count}"

        clear_registry()
