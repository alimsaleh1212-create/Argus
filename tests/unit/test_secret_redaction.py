"""Unit tests — T015: no SecretStr value ever appears in error output.

Any startup-path exception must name the offending key/path only.
The actual secret value must be absent from the exception message.
"""

from __future__ import annotations

import pytest

SENTINEL_SECRET_VALUE = "super-secret-value-1234"


class TestSecretRedaction:
    def test_secret_str_repr_is_masked(self) -> None:
        """pydantic SecretStr.__repr__ must not expose the value."""
        from pydantic import SecretStr

        s = SecretStr(SENTINEL_SECRET_VALUE)
        assert SENTINEL_SECRET_VALUE not in repr(s)
        assert SENTINEL_SECRET_VALUE not in str(s)

    def test_settings_repr_masks_vault_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Settings.__repr__ must not expose vault.token value."""
        monkeypatch.setenv("SENTINEL__VAULT__TOKEN", SENTINEL_SECRET_VALUE)
        from backend.infra.config import Settings

        s = Settings()
        assert SENTINEL_SECRET_VALUE not in repr(s)
        assert SENTINEL_SECRET_VALUE not in str(s)

    def test_settings_repr_masks_postgres_dsn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Settings.__repr__ must not expose postgres.dsn value."""
        monkeypatch.setenv(
            "SENTINEL__POSTGRES__DSN",
            f"postgresql+asyncpg://user:{SENTINEL_SECRET_VALUE}@host:5432/db",
        )
        from backend.infra.config import Settings

        s = Settings()
        assert SENTINEL_SECRET_VALUE not in repr(s)
        assert SENTINEL_SECRET_VALUE not in str(s)

    def test_unknown_key_error_does_not_contain_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unknown-key error message must not contain the actual secret value (FR-005)."""
        monkeypatch.setenv("SENTINEL__VAULT__TOKEN", SENTINEL_SECRET_VALUE)
        monkeypatch.setenv("SENTINEL__UNKNOWN_KEY", "boom")
        from backend.infra.config import load_settings

        with pytest.raises(ValueError) as exc_info:
            load_settings()

        error_text = str(exc_info.value)
        # Error must name the offending key, never the vault token value
        assert "SENTINEL__UNKNOWN_KEY" in error_text
        assert SENTINEL_SECRET_VALUE not in error_text
