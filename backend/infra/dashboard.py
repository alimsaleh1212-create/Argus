"""Dashboard infrastructure providers — auth_service and trace_repo.

Registered in main._bootstrap_providers() after vault and db are ready.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from backend.infra.logging import get_logger

logger = get_logger(__name__)


class AuthServiceProvider:
    """Reads dashboard creds from Vault and builds an AuthService singleton."""

    name = "auth_service"

    @contextlib.asynccontextmanager
    async def build(self, settings: Any) -> AsyncGenerator[Any, None]:
        from backend.infra.auth import AuthService

        cfg = settings.dashboard
        vault_addr = settings.vault.addr
        vault_token = settings.vault.token.get_secret_value()
        kv_mount = settings.vault.kv_mount
        path = cfg.vault_path_admin
        # Strip mount prefix if path stored as "secret/dashboard" with kv_mount="secret"
        clean = path.lstrip("/")
        mount_prefix = f"{kv_mount}/"
        if clean.startswith(mount_prefix):
            clean = clean[len(mount_prefix):]
        url = f"{vault_addr}/v1/{kv_mount}/data/{clean}"

        logger.info("auth_service_building")
        try:
            async with httpx.AsyncClient(timeout=settings.startup.dependency_timeout_s) as client:
                resp = await client.get(url, headers={"X-Vault-Token": vault_token})
        except Exception as exc:
            raise RuntimeError(
                f"Cannot reach Vault to load admin dashboard credentials from '{path}': "
                f"{type(exc).__name__}"
            ) from exc

        if resp.status_code == 404:
            raise RuntimeError(
                f"Dashboard admin credentials not found in Vault at '{path}' (404). "
                "Ensure vault-seed has run and set password_hash, salt, iterations, jwt_secret."
            )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Vault returned HTTP {resp.status_code} for '{path}'"
            )

        data = resp.json().get("data", {}).get("data", {})
        password_hash = data.get("password_hash", "")
        salt = data.get("salt", "")
        iterations_raw = data.get("iterations", "260000")
        jwt_secret = data.get("jwt_secret", "")

        if not (password_hash and salt and jwt_secret):
            raise RuntimeError(
                f"Vault path '{path}' is missing required fields "
                "(password_hash, salt, jwt_secret). Check vault-seed."
            )

        try:
            iterations = int(iterations_raw)
        except (ValueError, TypeError):
            iterations = 260000

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
            raise RuntimeError(
                "TraceRepoProvider requires db_engine to be registered first."
            )
        repo = TraceRepository(db.engine)
        logger.info("trace_repo_ready")
        try:
            yield repo
        finally:
            logger.info("trace_repo_disposed")
