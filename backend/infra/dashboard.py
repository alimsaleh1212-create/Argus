"""Dashboard infrastructure providers — auth_service and trace_repo.

Registered in main._bootstrap_providers() after vault and db are ready.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator
from typing import Any

from backend.infra.logging import get_logger

logger = get_logger(__name__)


class AuthServiceProvider:
    """Reads dashboard creds from Vault and builds an AuthService singleton."""

    name = "auth_service"

    @contextlib.asynccontextmanager
    async def build(self, settings: Any) -> AsyncGenerator[Any, None]:
        from backend.infra.auth import AuthService

        cfg = settings.dashboard
        path = cfg.vault_path_admin

        logger.info("auth_service_building")
        # secret/dashboard is in required_paths → read from the resolved singleton.
        container = getattr(settings, "_container", None)
        vault = getattr(container, "vault_client", None) if container else None
        if vault is None:
            raise RuntimeError(
                "AuthServiceProvider requires vault_client to be registered before it."
            )
        try:
            data = vault.get_secret(path)
        except KeyError as exc:
            raise RuntimeError(
                f"Dashboard admin credentials path '{path}' was not resolved at startup. "
                "Ensure it is in vault.required_paths and vault-seed wrote it."
            ) from exc

        password_hash = data.get("password_hash", "")
        salt = data.get("salt", "")
        iterations_raw = data.get("iterations", "260000")
        jwt_secret = data.get("jwt_secret", "")

        if not (password_hash and salt and jwt_secret):
            raise RuntimeError(
                f"Vault path '{path}' is missing required fields "
                "(password_hash, salt, jwt_secret). Check vault-seed."
            )

        # Fail loudly on a malformed iterations value rather than silently
        # substituting a default that would make every login fail confusingly.
        try:
            iterations = int(iterations_raw)
        except (ValueError, TypeError) as exc:
            raise RuntimeError(
                f"Vault path '{path}' has a non-integer 'iterations' value."
            ) from exc

        auth_service = AuthService(
            admin_username=cfg.admin_username,
            password_hash=password_hash,
            salt=salt,
            iterations=iterations,
            jwt_secret=jwt_secret,
            algorithm=cfg.algorithm,
            token_ttl_minutes=cfg.token_ttl_minutes,
        )
        logger.info("auth_service_ready")
        try:
            yield auth_service
        finally:
            logger.info("auth_service_disposed")


class TraceRepoProvider:
    """Exposes a TraceRepository singleton from the db_engine container attribute."""

    name = "trace_repo"

    @contextlib.asynccontextmanager
    async def build(self, settings: Any) -> AsyncGenerator[Any, None]:
        from backend.infra.db import DbEngine
        from backend.infra.trace_repository import TraceRepository

        db: DbEngine = getattr(settings, "_container", None) and getattr(
            settings._container, "db_engine", None
        )
        if db is None:
            raise RuntimeError("TraceRepoProvider requires db_engine to be registered first.")
        repo = TraceRepository(db.engine)
        logger.info("trace_repo_ready")
        try:
            yield repo
        finally:
            logger.info("trace_repo_disposed")
