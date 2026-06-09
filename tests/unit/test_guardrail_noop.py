"""Unit test for guardrail seam no-op-until-configured behaviour — T031."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.infra.intel import ThreatIntelClient


def _make_client() -> ThreatIntelClient:
    cache = AsyncMock()
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock()
    store = AsyncMock()
    store.write_fact = AsyncMock()
    redactor = MagicMock()
    redactor.redact_text = lambda text, boundary: text
    cfg = MagicMock()
    cfg.enabled = True
    cfg.source_name = "test"
    cfg.base_url = "http://test"
    cfg.timeout_s = 1.0
    cfg.cache_ttl_s = 60
    settings = MagicMock()
    settings.intel = cfg
    client = ThreatIntelClient(settings=settings, cache=cache, store=store, redactor=redactor)
    client.set_api_key("key")
    return client


@pytest.mark.asyncio
async def test_guardrail_not_configured_does_not_raise() -> None:
    """When get_guardrail() raises NotImplementedError, the intel path must not fail."""
    client = _make_client()

    with patch("backend.infra.guardrails.get_guardrail", side_effect=NotImplementedError):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.json = MagicMock(return_value={"verdict": "malicious"})
            mock_resp.raise_for_status = MagicMock()
            mock_ctx.get = AsyncMock(return_value=mock_resp)
            result = await client.lookup("1.2.3.4")

    # Guardrail not configured → lookup still completes, verdict returned
    assert result.verdict == "malicious"


@pytest.mark.asyncio
async def test_guardrail_error_does_not_raise() -> None:
    """Any guardrail error is swallowed and does not block the lookup."""
    client = _make_client()

    with patch("backend.infra.guardrails.get_guardrail", side_effect=RuntimeError("guardrail crash")):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.json = MagicMock(return_value={"verdict": "suspicious"})
            mock_resp.raise_for_status = MagicMock()
            mock_ctx.get = AsyncMock(return_value=mock_resp)
            result = await client.lookup("host.example")

    assert result.verdict == "suspicious"
