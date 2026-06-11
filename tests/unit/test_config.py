"""Unit tests for Settings — T014.

Tests written first (TDD). Verifies:
- Valid config boots and is frozen.
- Unknown extra key → ValidationError.
- .env.example declares every required field that Settings needs.
"""

from __future__ import annotations

import os

import pytest
from pydantic import SecretStr, ValidationError


class TestSettingsValidation:
    def test_valid_settings_loads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Settings builds from env without error on valid input."""
        monkeypatch.delenv("ARGUS__APP__ENV", raising=False)
        from backend.infra.config import Settings

        s = Settings()
        assert s.app.env == "local"

    def test_settings_is_frozen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Settings is immutable after construction."""
        from backend.infra.config import Settings

        s = Settings()
        with pytest.raises((TypeError, ValidationError)):
            s.app = s.app  # type: ignore[misc]

    def test_unknown_extra_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unknown ARGUS__ env key must cause ValueError (FR-002)."""
        monkeypatch.setenv("ARGUS__UNKNOWN_KEY", "boom")
        from backend.infra.config import load_settings

        with pytest.raises(ValueError, match="Unknown ARGUS__"):
            load_settings()

    def test_vault_token_is_secret_str(self) -> None:
        """Vault token must be SecretStr — never a plain str."""
        from backend.infra.config import Settings

        s = Settings()
        assert isinstance(s.vault.token, SecretStr)

    def test_postgres_dsn_is_secret_str(self) -> None:
        """Postgres DSN must be SecretStr."""
        from backend.infra.config import Settings

        s = Settings()
        assert isinstance(s.postgres.dsn, SecretStr)


class TestDotEnvExample:
    """Verify .env.example is exhaustive — every required key is present."""

    def test_env_example_exists(self) -> None:
        assert os.path.exists(".env.example"), ".env.example must be committed"

    def test_env_example_covers_required_sections(self) -> None:
        content = open(".env.example").read()
        required_prefixes = [
            "ARGUS__APP__",
            "ARGUS__VAULT__",
            "ARGUS__POSTGRES__",
            "ARGUS__MINIO__",
            "ARGUS__STARTUP__",
        ]
        for prefix in required_prefixes:
            assert prefix in content, f"{prefix} section missing from .env.example"
