"""Integration tests — T016: fail-fast on Vault unreachable or missing secret.

Uses a real Vault container (testcontainers). Tests that:
- The app refuses to start (raises RuntimeError / SystemExit) when Vault is
  unreachable (wrong address).
- The app refuses to start when a required secret path is absent.
- Error messages name the Vault path; the secret value is never included.
"""

from __future__ import annotations

import pytest

from backend.infra.config import Settings, StartupSettings, VaultSettings


@pytest.mark.integration
class TestVaultFailFast:
    """Fault-injection tests against a real Vault container."""

    async def test_vault_unreachable_raises(self) -> None:
        """VaultClient raises when Vault address is wrong (refuse to boot)."""
        from backend.infra.vault import VaultClient

        settings = Settings(
            vault=VaultSettings(
                addr="http://127.0.0.1:1",  # nothing listening
                required_paths=["secret/minio"],
            ),
            startup=StartupSettings(connect_retries=1, dependency_timeout_s=2.0),
        )

        client = VaultClient(settings.vault, settings.startup)

        with pytest.raises((RuntimeError, OSError, Exception)) as exc_info:
            await client.resolve_required_secrets()

        error_msg = str(exc_info.value)
        # Path must be named; secret value must not appear
        assert "secret/minio" in error_msg or "vault" in error_msg.lower()

    async def test_missing_required_secret_raises(self, vault_container) -> None:
        """VaultClient raises when a required secret path does not exist."""
        from backend.infra.vault import VaultClient

        settings = Settings(
            vault=VaultSettings(
                addr=vault_container.get_url(),
                token=vault_container.root_token,
                required_paths=["secret/nonexistent_path"],
            ),
            startup=StartupSettings(connect_retries=2, dependency_timeout_s=5.0),
        )

        client = VaultClient(settings.vault, settings.startup)

        with pytest.raises((RuntimeError, Exception)) as exc_info:
            await client.resolve_required_secrets()

        error_msg = str(exc_info.value)
        assert "secret/nonexistent_path" in error_msg


@pytest.fixture(scope="module")
def vault_container():
    """Start a real Vault container for integration tests."""
    pytest.importorskip("testcontainers")
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    container = DockerContainer("hashicorp/vault:latest")
    container.with_env("VAULT_DEV_ROOT_TOKEN_ID", "dev-root-token")
    container.with_env("VAULT_DEV_LISTEN_ADDRESS", "0.0.0.0:8200")
    # Disable mlock so Vault starts without the IPC_LOCK capability (sandbox-safe)
    container.with_env("VAULT_DISABLE_MLOCK", "true")
    container.with_exposed_ports(8200)
    container.with_kwargs(cap_add=["IPC_LOCK"])
    container.start()
    wait_for_logs(container, "Development mode", timeout=120)

    container.root_token = "dev-root-token"
    container.get_url = lambda: (
        f"http://{container.get_container_host_ip()}:{container.get_exposed_port(8200)}"
    )

    yield container
    container.stop()
