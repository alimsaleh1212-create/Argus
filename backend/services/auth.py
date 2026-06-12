"""Re-export shim — AuthService lives in infra to satisfy the layered contract."""

from backend.infra.auth import AuthError, AuthService

__all__ = ["AuthError", "AuthService"]
