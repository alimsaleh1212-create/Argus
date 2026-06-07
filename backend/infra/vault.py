"""Async Vault client (KV v2 over httpx) and VaultClientProvider.

Decision D4: uses httpx directly against Vault's HTTP API instead of hvac
(which is synchronous) to keep "async all the way down" (Constitution VII).
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import tenacity

from backend.infra.logging import get_logger

logger = get_logger(__name__)


class VaultClient:
    """Thin async wrapper around Vault KV v2.

    Resolves all required secret paths at startup; raises on any failure
    so the process refuses to boot (FR-003, FR-004).
    Error messages name the path only — never the value (FR-005).
    """

    def __init__(self, vault_cfg: Any, startup_cfg: Any) -> None:
        self._addr = vault_cfg.addr
        self._token = vault_cfg.token.get_secret_value()
        self._kv_mount = vault_cfg.kv_mount
        self._required_paths = vault_cfg.required_paths
        self._timeout = startup_cfg.dependency_timeout_s
        self._retries = startup_cfg.connect_retries
        self._resolved: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def resolve_required_secrets(self) -> None:
        """Fetch all required_paths from Vault; raises on any failure.

        Called once at startup by VaultClientProvider.build().
        """
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for path in self._required_paths:
                value = await self._fetch_with_retry(client, path)
                self._resolved[path] = value
                logger.info("vault_secret_resolved", path=path)

    def get_secret(self, path: str) -> str:
        """Return an already-resolved secret value by its KV path."""
        if path not in self._resolved:
            raise KeyError(f"Secret path '{path}' was not resolved at startup")
        return self._resolved[path]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _fetch_with_retry(self, client: httpx.AsyncClient, path: str) -> str:
        url = f"{self._addr}/v1/{self._kv_mount}/data/{path.lstrip('/')}"
        headers = {"X-Vault-Token": self._token}

        @tenacity.retry(
            stop=tenacity.stop_after_attempt(self._retries),
            wait=tenacity.wait_exponential(multiplier=0.2, max=2),
            retry=tenacity.retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException)),
            reraise=True,
        )
        async def _do_fetch() -> httpx.Response:
            return await client.get(url, headers=headers)

        try:
            response = await _do_fetch()
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise RuntimeError(
                f"Vault unreachable while fetching secret path '{path}': {type(exc).__name__}"
            ) from exc

        if response.status_code == 404:
            raise RuntimeError(
                f"Required secret path '{path}' not found in Vault (404). "
                "Ensure the secret exists before starting the application."
            )
        if response.status_code != 200:
            raise RuntimeError(
                f"Vault returned HTTP {response.status_code} for secret path '{path}'"
            )

        payload = response.json()
        data = payload.get("data", {}).get("data", {})
        if not data:
            raise RuntimeError(f"Required secret path '{path}' exists but contains no data fields")
        # Return the raw data dict as JSON string; consumers parse as needed.
        import json

        return json.dumps(data)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class VaultClientProvider:
    """Resolves all required_paths at startup; disposes cleanly on shutdown."""

    name = "vault_client"

    @contextlib.asynccontextmanager
    async def build(self, settings: Any) -> AsyncGenerator[VaultClient, None]:
        client = VaultClient(settings.vault, settings.startup)
        try:
            await client.resolve_required_secrets()
        except RuntimeError:
            raise  # Already has a secret-free message (FR-005)
        except Exception as exc:
            raise RuntimeError(
                f"Vault startup failed: {type(exc).__name__} — check vault.addr and vault.token"
            ) from exc

        logger.info(
            "vault_client_ready",
            required_paths=settings.vault.required_paths,
        )
        yield client
        logger.info("vault_client_disposed")


def register_vault_provider() -> None:
    """Register VaultClientProvider into the global registry."""
    from backend.infra.container import register_provider

    register_provider(VaultClientProvider())
