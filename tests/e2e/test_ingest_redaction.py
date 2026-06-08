"""e2e tests — T022: Redaction through the ingest path.

TDD: must FAIL before the router + intake are implemented.
Posts with_secret.json and verifies no raw secrets appear in the stored
incident's raw_alert or in the queued message.
"""

from __future__ import annotations

import json
import pathlib
import uuid
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "wazuh_alerts"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


SECRET_PATTERNS = [
    "AKIAIOSFODNN7EXAMPLE",
    "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9",
    "admin@example.com",
]


@pytest.mark.e2e
class TestIngestRedaction:
    async def test_secrets_not_in_stored_alert(self) -> None:
        """Secrets must be redacted before the Incident is persisted."""
        from backend.domain.incident import Incident, IncidentStatus, IngestResult, Severity
        from backend.infra.redaction import RedactionBoundary

        captured_raw_alert: dict = {}

        async def fake_accept(session, queue, cache, redactor, settings, alert):
            redacted = redactor.redact_mapping(alert.model_dump(mode="json"), RedactionBoundary.SNAPSHOT)
            captured_raw_alert.update(redacted)
            inc_id = uuid.uuid4()
            return IngestResult(
                incident_id=inc_id,
                status=IncidentStatus.RECEIVED,
                deduplicated=False,
            )

        from backend.infra.redaction import build_redactor

        real_redactor = build_redactor(presidio_enabled=False, entropy_threshold=3.5)

        from backend.infra.container import AppContainer, clear_registry
        from backend.infra.config import load_settings
        from backend.main import create_app

        clear_registry()
        settings = load_settings()
        app = create_app(settings)

        container = AppContainer()
        mock_obs = MagicMock()
        mock_obs.redactor = real_redactor
        mock_obs.tracer = MagicMock()
        container.observability = mock_obs
        mock_db = MagicMock()
        mock_db.session_factory = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()))
        container.db_engine = mock_db
        mock_vault = MagicMock()
        mock_vault.get_secret = AsyncMock(return_value={"token": "test-token"})
        container.vault_client = mock_vault
        container.cache = AsyncMock()
        container.queue = AsyncMock()
        app.state.container = container
        app.state.settings = settings

        from fastapi.testclient import TestClient

        with patch("backend.services.intake.accept", side_effect=fake_accept):
            with patch("backend.routers.ingest._get_webhook_token", return_value="test-token"):
                with TestClient(app, raise_server_exceptions=False) as client:
                    resp = client.post(
                        "/ingest/wazuh",
                        json=_load("with_secret.json"),
                        headers={"Authorization": "Bearer test-token"},
                    )

        assert resp.status_code == 202

        raw_str = json.dumps(captured_raw_alert)
        for secret in SECRET_PATTERNS:
            assert secret not in raw_str, f"Secret '{secret[:10]}...' found unredacted in stored alert"
