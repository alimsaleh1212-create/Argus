"""e2e tests — T021: POST /ingest/wazuh endpoint.

TDD: must FAIL before the router + intake are implemented.
"""

from __future__ import annotations

import json
import pathlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "wazuh_alerts"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _make_app(redis_up: bool = True):
    """Build a minimal test app with providers stubbed out."""
    import os

    os.environ.setdefault("SENTINEL__REDIS__URL", "redis://localhost:6379/0")

    from backend.infra.container import AppContainer, clear_registry
    from backend.infra.config import load_settings
    from backend.main import create_app

    clear_registry()
    settings = load_settings()
    app = create_app(settings)

    # Stub all providers
    container = AppContainer()

    # Minimal mocks
    mock_obs = MagicMock()
    mock_obs.redactor.redact_mapping = lambda x, *a, **kw: x
    mock_obs.tracer = MagicMock()
    container.observability = mock_obs

    mock_db = MagicMock()
    mock_db.session_factory = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()))
    container.db_engine = mock_db

    mock_vault = MagicMock()
    mock_vault.get_secret = AsyncMock(return_value={"token": "test-webhook-token"})
    container.vault_client = mock_vault

    mock_cache = AsyncMock() if redis_up else None
    container.cache = mock_cache

    mock_queue = AsyncMock()
    if not redis_up:
        mock_queue.enqueue = AsyncMock(side_effect=RuntimeError("Redis down"))
    else:
        mock_queue.enqueue = AsyncMock(return_value="job-id")
    container.queue = mock_queue

    app.state.container = container
    app.state.settings = settings
    return app


@pytest.mark.e2e
class TestIngestEndpoint:
    def test_valid_alert_returns_202(self) -> None:
        from backend.domain.incident import Incident, IncidentStatus, Severity, IngestResult

        fake_incident = Incident(
            id=uuid.uuid4(),
            status=IncidentStatus.RECEIVED,
            severity=Severity.HIGH,
            correlation_id="corr-1",
            dedup_fingerprint="fp-1",
            source="wazuh",
            raw_alert={"rule": {"level": 10}},
        )
        fake_result = IngestResult(
            incident_id=fake_incident.id,
            status=IncidentStatus.RECEIVED,
            deduplicated=False,
        )

        app = _make_app(redis_up=True)
        with patch("backend.services.intake.accept", new_callable=AsyncMock, return_value=fake_result):
            with patch("backend.routers.ingest._get_webhook_token", return_value="test-webhook-token"):
                with TestClient(app, raise_server_exceptions=False) as client:
                    resp = client.post(
                        "/ingest/wazuh",
                        json=_load("ssh_bruteforce.json"),
                        headers={"Authorization": "Bearer test-webhook-token"},
                    )
        assert resp.status_code == 202

    def test_missing_token_returns_401(self) -> None:
        app = _make_app(redis_up=True)
        with patch("backend.routers.ingest._get_webhook_token", return_value="test-webhook-token"):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post(
                    "/ingest/wazuh",
                    json=_load("ssh_bruteforce.json"),
                )
        assert resp.status_code == 401

    def test_malformed_body_returns_422(self) -> None:
        app = _make_app(redis_up=True)
        with patch("backend.routers.ingest._get_webhook_token", return_value="test-webhook-token"):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post(
                    "/ingest/wazuh",
                    json=_load("malformed.json"),
                    headers={"Authorization": "Bearer test-webhook-token"},
                )
        assert resp.status_code == 422

    def test_oversize_body_returns_413(self) -> None:
        app = _make_app(redis_up=True)
        with patch("backend.routers.ingest._get_webhook_token", return_value="test-webhook-token"):
            with TestClient(app, raise_server_exceptions=False) as client:
                big_payload = {"rule": {"level": 5}, "data": {"x": "A" * 300_000}}
                resp = client.post(
                    "/ingest/wazuh",
                    content=json.dumps(big_payload).encode(),
                    headers={
                        "Authorization": "Bearer test-webhook-token",
                        "Content-Type": "application/json",
                    },
                )
        assert resp.status_code == 413

    def test_redis_down_returns_503_no_orphan(self) -> None:
        app = _make_app(redis_up=False)
        with patch("backend.routers.ingest._get_webhook_token", return_value="test-webhook-token"):
            with patch("backend.services.intake.accept", new_callable=AsyncMock, side_effect=RuntimeError("Redis down")):
                with TestClient(app, raise_server_exceptions=False) as client:
                    resp = client.post(
                        "/ingest/wazuh",
                        json=_load("ssh_bruteforce.json"),
                        headers={"Authorization": "Bearer test-webhook-token"},
                    )
        assert resp.status_code == 503
