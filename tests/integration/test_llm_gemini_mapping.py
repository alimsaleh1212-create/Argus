"""Integration tests — T009 [US1]: Gemini request/response mapping.

Always-runs: mocked HTTP via respx/httpx mock.
Live test: gated on GEMINI_API_KEY presence (skipped in keyless CI).
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.domain.llm import (
    LlmMessage,
    LlmRequest,
    LlmResponse,
    ProviderId,
    StopReason,
    TokenUsage,
)


def _gemini_key_present() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


@pytest.mark.integration
class TestGeminiDriverMapping:
    async def test_request_response_mapping_mocked(self) -> None:
        """GeminiDriver maps uniform request/response correctly via mocked SDK."""
        from backend.infra.llm_drivers import GeminiDriver
        from backend.infra.config import LlmSettings

        settings = LlmSettings(gemini_model="gemini-1.5-flash")

        # Mock the google-genai client
        mock_response = MagicMock()
        mock_response.text = "Hello from Gemini"
        mock_response.function_calls = []
        mock_response.usage_metadata = MagicMock()
        mock_response.usage_metadata.prompt_token_count = 10
        mock_response.usage_metadata.candidates_token_count = 5
        mock_response.candidates = [MagicMock()]
        mock_response.candidates[0].finish_reason = MagicMock()
        mock_response.candidates[0].finish_reason.name = "STOP"

        with patch("backend.infra.llm_drivers.genai") as mock_genai:
            mock_client = MagicMock()
            mock_genai.Client.return_value = mock_client
            mock_client.aio = MagicMock()
            mock_client.aio.models = MagicMock()
            mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

            driver = GeminiDriver(settings, api_key="fake-key")
            req = LlmRequest(messages=[LlmMessage(role="user", content="Hello")])
            resp = await driver.generate(req)

        assert isinstance(resp, LlmResponse)
        assert resp.provider == ProviderId.GEMINI
        assert resp.content == "Hello from Gemini"
        assert resp.usage.prompt_tokens == 10
        assert resp.usage.completion_tokens == 5
        assert resp.stop_reason == StopReason.END_TURN

    async def test_usage_normalization_mocked(self) -> None:
        """Gemini usage_metadata → TokenUsage.prompt_tokens / completion_tokens."""
        from backend.infra.llm_drivers import GeminiDriver
        from backend.infra.config import LlmSettings

        settings = LlmSettings(gemini_model="gemini-1.5-flash")

        mock_response = MagicMock()
        mock_response.text = "ok"
        mock_response.function_calls = []
        mock_response.usage_metadata = MagicMock()
        mock_response.usage_metadata.prompt_token_count = 42
        mock_response.usage_metadata.candidates_token_count = 7
        mock_response.candidates = [MagicMock()]
        mock_response.candidates[0].finish_reason = MagicMock()
        mock_response.candidates[0].finish_reason.name = "STOP"

        with patch("backend.infra.llm_drivers.genai") as mock_genai:
            mock_client = MagicMock()
            mock_genai.Client.return_value = mock_client
            mock_client.aio = MagicMock()
            mock_client.aio.models = MagicMock()
            mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

            driver = GeminiDriver(settings, api_key="fake-key")
            req = LlmRequest(messages=[LlmMessage(role="user", content="hello")])
            resp = await driver.generate(req)

        assert resp.usage.prompt_tokens == 42
        assert resp.usage.completion_tokens == 7

    @pytest.mark.skipif(not _gemini_key_present(), reason="GEMINI_API_KEY not set — live test skipped")
    async def test_live_gemini_smoke(self) -> None:
        """Live Gemini smoke test — requires GEMINI_API_KEY env var."""
        from backend.infra.llm_drivers import GeminiDriver
        from backend.infra.config import LlmSettings

        api_key = os.environ["GEMINI_API_KEY"]
        settings = LlmSettings(gemini_model="gemini-1.5-flash")
        driver = GeminiDriver(settings, api_key=api_key)
        req = LlmRequest(messages=[LlmMessage(role="user", content="Reply with the single word: pong")])
        resp = await driver.generate(req)

        assert isinstance(resp, LlmResponse)
        assert resp.provider == ProviderId.GEMINI
        assert resp.content
        assert resp.stop_reason in StopReason
