"""Integration tests — T008 [US1]: real Ollama via compose service (tiny model).

Requires a running Ollama service. Skipped when Ollama is not reachable.
Mark: integration
"""

from __future__ import annotations

import pytest

from backend.domain.llm import (
    LlmMessage,
    LlmRequest,
    LlmResponse,
    ProviderId,
    StopReason,
)


def _ollama_available() -> bool:
    """True only when Ollama is reachable AND the configured model is pulled."""
    try:
        import httpx

        resp = httpx.get("http://localhost:11434/api/tags", timeout=3.0)
        if resp.status_code != 200:
            return False
        models = [m.get("name", "") for m in resp.json().get("models", [])]
        return any("qwen2" in m for m in models)
    except Exception:
        return False


@pytest.mark.integration
@pytest.mark.skipif(not _ollama_available(), reason="Ollama service not reachable")
class TestOllamaIntegration:
    async def test_real_generate_returns_uniform_response(self) -> None:
        """A real Ollama generate returns the uniform LlmResponse shape."""
        from backend.infra.config import LlmSettings
        from backend.infra.llm_drivers import OllamaDriver

        settings = LlmSettings(ollama_base_url="http://localhost:11434", ollama_model="qwen2:0.5b")
        driver = OllamaDriver(settings)

        req = LlmRequest(messages=[LlmMessage(role="user", content="Say the word 'hello' only.")])
        resp = await driver.generate(req)

        assert isinstance(resp, LlmResponse)
        assert resp.provider == ProviderId.OLLAMA
        assert resp.model
        assert resp.stop_reason in StopReason
        assert isinstance(resp.content, str)

    async def test_usage_normalized_to_expected_fields(self) -> None:
        """Ollama usage normalizes to prompt_tokens / completion_tokens (may be None)."""
        from backend.infra.config import LlmSettings
        from backend.infra.llm_drivers import OllamaDriver

        settings = LlmSettings(ollama_base_url="http://localhost:11434", ollama_model="qwen2:0.5b")
        driver = OllamaDriver(settings)

        req = LlmRequest(messages=[LlmMessage(role="user", content="Reply: yes")])
        resp = await driver.generate(req)

        # Usage fields exist (may be None if provider omits)
        assert hasattr(resp.usage, "prompt_tokens")
        assert hasattr(resp.usage, "completion_tokens")

    async def test_structured_output_honored_or_flagged(self) -> None:
        """A structured-output request is honored or raises CONTRACT_UNSATISFIED (FR-004)."""
        import json

        from backend.domain.llm import LlmError, LlmErrorKind
        from backend.infra.config import LlmSettings
        from backend.infra.llm_drivers import OllamaDriver

        settings = LlmSettings(ollama_base_url="http://localhost:11434", ollama_model="qwen2:0.5b")
        driver = OllamaDriver(settings)
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        }
        req = LlmRequest(
            messages=[LlmMessage(role="user", content='Reply with JSON: {"answer": "yes"}')],
            response_schema=schema,
        )
        try:
            resp = await driver.generate(req)
            # If it succeeded, content should be valid JSON matching the schema
            parsed = json.loads(resp.content)
            assert "answer" in parsed
        except LlmError as e:
            # If validation failed at the driver level, that's acceptable too
            assert e.kind in (LlmErrorKind.CONTRACT_UNSATISFIED, LlmErrorKind.TRANSIENT)
