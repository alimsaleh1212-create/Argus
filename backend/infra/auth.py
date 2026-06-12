"""Auth infrastructure — PBKDF2 password verify + HS256 JWT issue/verify.

Pure, synchronous, testable. No FastAPI dependencies.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from jwt.exceptions import InvalidTokenError


class AuthError(Exception):
    """Raised for any authentication failure (bad creds, expired token, tampered sig)."""


class AuthService:
    """Stateless auth: verify PBKDF2-hashed passwords + issue/verify HS256 JWTs."""

    def __init__(
        self,
        *,
        admin_username: str,
        password_hash: str,
        salt: str,
        iterations: int,
        jwt_secret: str,
        algorithm: str,
        token_ttl_minutes: int,
    ) -> None:
        self._admin_username = admin_username
        self._password_hash = password_hash
        self._salt = salt
        self._iterations = iterations
        self._jwt_secret = jwt_secret
        self._algorithm = algorithm
        self._token_ttl_minutes = token_ttl_minutes

    def verify_credentials(self, username: str, password: str) -> bool:
        """Constant-time credential check. Returns True iff username + password match.

        Uses PBKDF2-HMAC-SHA256 + hmac.compare_digest to prevent both
        timing attacks and username enumeration.
        """
        candidate_hash = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            self._salt.encode(),
            self._iterations,
        ).hex()
        username_ok = hmac.compare_digest(username, self._admin_username)
        password_ok = hmac.compare_digest(candidate_hash, self._password_hash)
        return username_ok and password_ok

    def issue_token(self, subject: str, role: str) -> tuple[str, int]:
        """Mint an HS256 JWT. Returns (token, expires_in_seconds)."""
        now = datetime.now(UTC)
        exp = now + timedelta(minutes=self._token_ttl_minutes)
        payload: dict[str, Any] = {
            "sub": subject,
            "role": role,
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
        }
        token = jwt.encode(payload, self._jwt_secret, algorithm=self._algorithm)
        return token, self._token_ttl_minutes * 60

    def verify_token(self, token: str) -> dict[str, Any]:
        """Decode and validate a JWT. Raises AuthError on any failure."""
        try:
            payload = jwt.decode(
                token,
                self._jwt_secret,
                algorithms=[self._algorithm],
                options={"require": ["sub", "role", "exp"]},
            )
        except InvalidTokenError as exc:
            raise AuthError(str(exc)) from exc
        return payload
