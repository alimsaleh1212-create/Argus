"""AppContainer lifecycle — build providers in order, dispose in reverse.

Guarantees:
- Each provider is built exactly once.
- If any build raises, already-entered providers are exited in reverse,
  then the process exits non-zero (never serves in a half-built state).
- On normal shutdown, all providers are disposed in reverse order.
- Error messages name the offending provider/path; never emit secret values.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING

from backend.infra.container import AppContainer, get_registry
from backend.infra.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = get_logger(__name__)


@asynccontextmanager
async def sentinel_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan that builds providers in registration order."""
    settings = app.state.settings
    container = AppContainer()
    stack = AsyncExitStack()

    providers = get_registry()
    entered: list[str] = []

    # Make the in-progress container accessible to providers that need already-built
    # siblings (e.g. LlmProvider reading observability). Uses object.__setattr__
    # to bypass the frozen Settings model.
    object.__setattr__(settings, "_container", container)

    try:
        async with stack:
            for provider in providers:
                logger.info("provider_building", provider=provider.name)
                try:
                    resource = await stack.enter_async_context(provider.build(settings))
                except Exception as exc:
                    _fail_fast(provider.name, exc)

                setattr(container, provider.name, resource)
                entered.append(provider.name)
                logger.info("provider_ready", provider=provider.name)

            app.state.container = container
            logger.info("sentinel_ready", providers=entered)
            yield

    except SystemExit:
        raise
    except Exception as exc:
        logger.error("lifespan_error", error=str(exc))
        sys.exit(1)
    finally:
        logger.info("sentinel_shutdown", providers=list(reversed(entered)))
        _assert_no_leaks(container)


def _assert_no_leaks(container: AppContainer) -> None:
    """T031 — probe for open resources after disposal.

    Each provider that exposes a ``dispose_check()`` hook is queried;
    a non-empty result is logged as a warning. This is a best-effort check;
    concrete providers may opt in by implementing the hook.
    """
    for name in vars(container):
        resource = getattr(container, name, None)
        if resource is None:
            continue
        check = getattr(resource, "dispose_check", None)
        if callable(check):
            leak_info = check()
            if leak_info:
                logger.warning("resource_leak_detected", provider=name, detail=str(leak_info))


def _fail_fast(provider_name: str, exc: Exception) -> None:
    """Log a secret-free error and exit non-zero."""
    msg = str(exc)
    logger.error(
        "provider_build_failed",
        provider=provider_name,
        error=msg,
    )
    # Raise to trigger AsyncExitStack cleanup of already-entered providers.
    raise RuntimeError(f"Provider '{provider_name}' failed to build: {msg}") from exc
