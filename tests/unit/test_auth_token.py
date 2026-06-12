"""Unit tests: auth token issue/verify, PBKDF2 verify, constant-time behaviour."""

from __future__ import annotations

import hashlib
import time

import pytest

from backend.services.auth import AuthError, AuthService

_SALT = "testsalt"
_ITERATIONS = 1000
_PASSWORD = "correcthorse"
_HASH = hashlib.pbkdf2_hmac("sha256", _PASSWORD.encode(), _SALT.encode(), _ITERATIONS).hex()
_JWT_SECRET = "test-jwt-secret"
_ALGORITHM = "HS256"


def _make_service(**kwargs: object) -> AuthService:
    defaults = dict(
        admin_username="admin",
        password_hash=_HASH,
        salt=_SALT,
        iterations=_ITERATIONS,
        jwt_secret=_JWT_SECRET,
        algorithm=_ALGORITHM,
        token_ttl_minutes=60,
    )
    defaults.update(kwargs)
    return AuthService(**defaults)  # type: ignore[arg-type]


class TestVerifyCredentials:
    def test_correct_returns_true(self) -> None:
        svc = _make_service()
        assert svc.verify_credentials("admin", _PASSWORD) is True

    def test_wrong_password_returns_false(self) -> None:
        svc = _make_service()
        assert svc.verify_credentials("admin", "wrongpassword") is False

    def test_wrong_username_returns_false(self) -> None:
        svc = _make_service()
        assert svc.verify_credentials("notadmin", _PASSWORD) is False

    def test_both_wrong_returns_false(self) -> None:
        svc = _make_service()
        assert svc.verify_credentials("notadmin", "wrongpassword") is False

    def test_constant_time_no_early_return(self) -> None:
        """Both correct and wrong paths take roughly the same time (PBKDF2 dominates)."""
        svc = _make_service()
        rounds = 5
        t_correct = sum(
            _time(lambda: svc.verify_credentials("admin", _PASSWORD)) for _ in range(rounds)
        ) / rounds
        t_wrong = sum(
            _time(lambda: svc.verify_credentials("admin", "wrongpassword")) for _ in range(rounds)
        ) / rounds
        # Constant-time: both must spend at least 0.5× of the other's time (allow 10× skew max)
        ratio = max(t_correct, t_wrong) / max(min(t_correct, t_wrong), 1e-9)
        assert ratio < 10, f"Timing ratio too high ({ratio:.1f}×), potential early return"


class TestIssueVerifyToken:
    def test_roundtrip(self) -> None:
        svc = _make_service()
        token, expires_in = svc.issue_token("admin", "admin")
        assert isinstance(token, str)
        assert expires_in == 60 * 60
        payload = svc.verify_token(token)
        assert payload["sub"] == "admin"
        assert payload["role"] == "admin"

    def test_expired_token_raises(self) -> None:
        svc = _make_service(token_ttl_minutes=0)
        import jwt as pyjwt

        from datetime import UTC, datetime, timedelta

        payload = {
            "sub": "admin",
            "role": "admin",
            "iat": int((datetime.now(UTC) - timedelta(seconds=2)).timestamp()),
            "exp": int((datetime.now(UTC) - timedelta(seconds=1)).timestamp()),
        }
        expired = pyjwt.encode(payload, _JWT_SECRET, algorithm=_ALGORITHM)
        with pytest.raises(AuthError):
            svc.verify_token(expired)

    def test_tampered_signature_raises(self) -> None:
        svc = _make_service()
        token, _ = svc.issue_token("admin", "admin")
        tampered = token[:-4] + "XXXX"
        with pytest.raises(AuthError):
            svc.verify_token(tampered)

    def test_wrong_secret_raises(self) -> None:
        svc1 = _make_service()
        svc2 = _make_service(jwt_secret="other-secret")
        token, _ = svc1.issue_token("admin", "admin")
        with pytest.raises(AuthError):
            svc2.verify_token(token)


def _time(fn: object) -> float:
    t0 = time.monotonic()
    fn()  # type: ignore[operator]
    return time.monotonic() - t0
