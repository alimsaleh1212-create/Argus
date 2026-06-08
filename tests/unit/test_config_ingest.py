"""Unit tests — T006: RedisSettings + IngestSettings.

TDD: these must FAIL before infra/config.py is updated.
"""

from __future__ import annotations

import pytest


class TestRedisSettings:
    def test_defaults(self) -> None:
        from backend.infra.config import RedisSettings

        s = RedisSettings()
        assert s.url == "redis://redis:6379/0"
        assert s.queue_key == "queue:incidents"
        assert s.processing_key == "queue:processing"
        assert s.dedup_prefix == "dedup:"
        assert s.dequeue_block_s == 5.0

    def test_extra_forbid(self) -> None:
        from pydantic import ValidationError

        from backend.infra.config import RedisSettings

        with pytest.raises(ValidationError):
            RedisSettings.model_validate({"url": "redis://localhost", "unknown_key": "x"})


class TestIngestSettings:
    def test_defaults(self) -> None:
        from backend.infra.config import IngestSettings

        s = IngestSettings()
        assert s.webhook_vault_path == "secret/ingest"
        assert s.max_alert_bytes == 262_144
        assert s.dedup_window_s == 300
        assert s.max_attempts == 3

    def test_extra_forbid(self) -> None:
        from pydantic import ValidationError

        from backend.infra.config import IngestSettings

        with pytest.raises(ValidationError):
            IngestSettings.model_validate({"max_alert_bytes": 1024, "bad_field": True})


class TestSettingsIntegration:
    def test_redis_and_ingest_registered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Settings accepts 'redis' and 'ingest' as known sections."""
        monkeypatch.setenv("SENTINEL__REDIS__URL", "redis://localhost:6379/1")
        monkeypatch.setenv("SENTINEL__INGEST__MAX_ALERT_BYTES", "131072")
        from backend.infra.config import Settings

        s = Settings()
        assert s.redis.url == "redis://localhost:6379/1"
        assert s.ingest.max_alert_bytes == 131_072

    def test_redis_section_not_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """'redis' is a known section — must not trigger the unknown-key error."""
        monkeypatch.setenv("SENTINEL__REDIS__URL", "redis://localhost:6379/0")
        from backend.infra.config import load_settings

        # Should not raise
        s = load_settings()
        assert s.redis is not None

    def test_ingest_section_not_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """'ingest' is a known section — must not trigger the unknown-key error."""
        monkeypatch.setenv("SENTINEL__INGEST__MAX_ATTEMPTS", "5")
        from backend.infra.config import load_settings

        s = load_settings()
        assert s.ingest is not None

    def test_webhook_vault_path_appended_to_required_paths(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ingest.webhook_vault_path must appear in vault.required_paths after model_validator."""
        monkeypatch.delenv("SENTINEL__VAULT__REQUIRED_PATHS", raising=False)
        from backend.infra.config import Settings

        s = Settings()
        assert "secret/ingest" in s.vault.required_paths
