"""Unit tests for ThreatIntelClient — T025.

All external dependencies (httpx, Redis, store) are mocked.
Redactor is a lightweight pass-through mock to avoid loading spacy/presidio.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.infra.intel import ThreatIntelClient


def _make_settings(
    enabled: bool = True, base_url: str = "http://intel.test", timeout_s: float = 1.0
):
    cfg = MagicMock()
    cfg.enabled = enabled
    cfg.source_name = "test-intel"
    cfg.base_url = base_url
    cfg.timeout_s = timeout_s
    cfg.cache_ttl_s = 60
    settings = MagicMock()
    settings.intel = cfg
    return settings


def _make_redactor():
    r = MagicMock()
    r.redact_text = lambda text, boundary: text
    return r


def _make_client(enabled: bool = True, api_key: str = "test-key") -> ThreatIntelClient:
    cache = AsyncMock()
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock()
    store = AsyncMock()
    store.write_fact = AsyncMock()
    client = ThreatIntelClient(
        settings=_make_settings(enabled=enabled),
        cache=cache,
        store=store,
        redactor=_make_redactor(),
    )
    if api_key:
        client.set_api_key(api_key)
    return client


# ── Disabled fast-path ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_returns_unknown_no_call() -> None:
    client = _make_client(enabled=False)
    with patch("httpx.AsyncClient") as mock_http:
        result = await client.lookup("1.2.3.4")
    assert result.verdict == "unknown"
    mock_http.assert_not_called()


@pytest.mark.asyncio
async def test_no_api_key_returns_unknown_no_call() -> None:
    client = _make_client(api_key="")
    with patch("httpx.AsyncClient") as mock_http:
        result = await client.lookup("1.2.3.4")
    assert result.verdict == "unknown"
    mock_http.assert_not_called()


# ── Cache hit ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_hit_no_external_call() -> None:
    client = _make_client()
    client._cache.get = AsyncMock(return_value="malicious")
    with patch("httpx.AsyncClient") as mock_http:
        result = await client.lookup("1.2.3.4")
    assert result.verdict == "malicious"
    mock_http.assert_not_called()


# ── Timeout / HTTP error → unknown ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_returns_unknown() -> None:
    import httpx as _httpx

    client = _make_client()
    with patch("httpx.AsyncClient") as mock_http_cls:
        mock_ctx = AsyncMock()
        mock_http_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_http_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.get = AsyncMock(side_effect=_httpx.TimeoutException("timeout"))
        result = await client.lookup("1.2.3.4")
    assert result.verdict == "unknown"


@pytest.mark.asyncio
async def test_http_error_returns_unknown() -> None:
    import httpx as _httpx

    client = _make_client()
    with patch("httpx.AsyncClient") as mock_http_cls:
        mock_ctx = AsyncMock()
        mock_http_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_http_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.get = AsyncMock(
            side_effect=_httpx.HTTPStatusError(
                "500", request=MagicMock(), response=MagicMock(status_code=500)
            )
        )
        result = await client.lookup("1.2.3.4")
    assert result.verdict == "unknown"


# ── Non-unknown verdict calls write_fact ─────────────────────────────────────


@pytest.mark.asyncio
async def test_non_unknown_verdict_calls_write_fact() -> None:
    client = _make_client()
    with patch("httpx.AsyncClient") as mock_http_cls:
        mock_ctx = AsyncMock()
        mock_http_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_http_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.json = MagicMock(return_value={"verdict": "malicious"})
        mock_resp.raise_for_status = MagicMock()
        mock_ctx.get = AsyncMock(return_value=mock_resp)
        await client.lookup("1.2.3.4")
    client._store.write_fact.assert_called_once()


@pytest.mark.asyncio
async def test_unknown_verdict_does_not_call_write_fact() -> None:
    client = _make_client()
    with patch("httpx.AsyncClient") as mock_http_cls:
        mock_ctx = AsyncMock()
        mock_http_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_http_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.json = MagicMock(return_value={"verdict": "unknown"})
        mock_resp.raise_for_status = MagicMock()
        mock_ctx.get = AsyncMock(return_value=mock_resp)
        await client.lookup("1.2.3.4")
    client._store.write_fact.assert_not_called()


# ── Redaction: planted secret never appears in written fact or cache ─────────


@pytest.mark.asyncio
async def test_redaction_applied_before_cache_and_write() -> None:
    secret = "AKIAIOSFODNN7EXAMPLE"
    captured_cache_keys: list[str] = []
    captured_facts: list = []

    cache = AsyncMock()
    cache.get = AsyncMock(return_value=None)

    async def _cache_set(key, value, ex=None):
        captured_cache_keys.append(key)

    cache.set = AsyncMock(side_effect=_cache_set)

    store = AsyncMock()

    async def _write_fact(fact):
        captured_facts.append(fact)

    store.write_fact = AsyncMock(side_effect=_write_fact)

    redactor = MagicMock()
    redactor.redact_text = lambda text, boundary: text.replace(secret, "[REDACTED:CREDENTIAL]")

    client = ThreatIntelClient(
        settings=_make_settings(),
        cache=cache,
        store=store,
        redactor=redactor,
    )
    client.set_api_key("test-key")

    with patch("httpx.AsyncClient") as mock_http_cls:
        mock_ctx = AsyncMock()
        mock_http_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_http_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.json = MagicMock(return_value={"verdict": "malicious"})
        mock_resp.raise_for_status = MagicMock()
        mock_ctx.get = AsyncMock(return_value=mock_resp)
        await client.lookup(f"host-{secret}")

    for key in captured_cache_keys:
        assert secret not in key, f"Secret leaked into cache key: {key}"
    for fact in captured_facts:
        assert secret not in fact.entity.value, f"Secret leaked into written fact: {fact}"
