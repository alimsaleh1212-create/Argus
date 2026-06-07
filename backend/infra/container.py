"""Provider protocol and ordered provider registry — the extension seam.

Later specs append their own Provider without modifying this file or lifespan.py.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, runtime_checkable

from backend.infra.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Provider(Protocol):
    """A startup-initialized singleton resource.

    ``build()`` must be an async context manager that yields exactly one
    constructed resource and disposes it on context exit.
    """

    name: str

    def build(self, settings: Any) -> AbstractAsyncContextManager[Any]:
        """Yield a single constructed resource; dispose it on context exit."""
        ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_registry: list[Provider] = []


def register_provider(provider: Provider) -> None:
    """Append a provider to the ordered registry.

    Order of registration = order of startup; teardown is reverse.
    Raises ``ValueError`` if the name is already registered (fail-fast).
    """
    for existing in _registry:
        if existing.name == provider.name:
            raise ValueError(
                f"Provider name '{provider.name}' is already registered. "
                "Each provider must have a unique name."
            )
    _registry.append(provider)
    logger.debug("provider_registered", provider=provider.name)


def get_registry() -> list[Provider]:
    """Return a snapshot of the current registry (ordered)."""
    return list(_registry)


def clear_registry() -> None:
    """Clear all registered providers — for use in tests only."""
    _registry.clear()


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------


class AppContainer:
    """Holds one built singleton per registered provider name.

    Attributes are set dynamically during lifespan startup and removed on
    shutdown. Consumers read them via ``app.state.container.<name>``.
    """
