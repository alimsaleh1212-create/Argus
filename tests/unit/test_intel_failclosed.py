"""Unit tests for ThreatIntelClient fail-closed behaviour — T028."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.infra.intel import ThreatIntelClient


def _make_settings(base_url: str = "http://dead-host.invalid"):
    cfg = MagicMock()
    cfg.enabled = True
    cfg.source_name = "test-intel"
    cfg.base_url = base_url
    cfg.timeout_s = 0.1
    cfg.cache_ttl_s = 60
    settings = MagicMock()
    settings.intel = cfg
    return settings


def _make_client(base_url: str = "http://dead-host.invalid") -> ThreatIntelClient:
    cache = AsyncMock()
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock()
    store = AsyncMock()
    store.write_fact = AsyncMock()
    redactor = MagicMock()
    redactor.redact_text = lambda text, boundary: text
    client = ThreatIntelClient(
        settings=_make_settings(base_url=base_url),
        cache=cache,
        store=store,
        redactor=redactor,
    )
    client.set_api_key("test-key")
    return client


@pytest.mark.asyncio
async def test_dead_host_returns_unknown() -> None:
    import httpx

    client = _make_client()
    with patch("httpx.AsyncClient") as mock_cls:
        mock_ctx = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        result = await client.lookup("1.2.3.4")
    assert result.verdict == "unknown"


@pytest.mark.asyncio
async def test_forced_timeout_returns_unknown() -> None:
    import httpx

    client = _make_client()
    with patch("httpx.AsyncClient") as mock_cls:
        mock_ctx = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        result = await client.lookup("1.2.3.4")
    assert result.verdict == "unknown"


@pytest.mark.asyncio
async def test_write_fact_error_swallowed_verdict_still_returned() -> None:
    """A write_fact error must not propagate — verdict is still returned."""

    client = _make_client()
    client._store.write_fact = AsyncMock(side_effect=RuntimeError("neo4j down"))

    with patch("httpx.AsyncClient") as mock_cls:
        mock_ctx = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.json = MagicMock(return_value={"verdict": "malicious"})
        mock_resp.raise_for_status = MagicMock()
        mock_ctx.get = AsyncMock(return_value=mock_resp)
        result = await client.lookup("1.2.3.4")

    assert result.verdict == "malicious"


@pytest.mark.asyncio
async def test_lookup_never_raises_into_caller() -> None:
    """lookup() must never raise regardless of underlying failure."""
    client = _make_client()

    with patch.object(client, "_fetch_verdict", AsyncMock(side_effect=RuntimeError("boom"))):
        result = await client.lookup("1.2.3.4")

    assert result.verdict == "unknown"
