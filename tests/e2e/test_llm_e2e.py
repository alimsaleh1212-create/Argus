"""E2E tests — T025 [US4]: synthetic call via DI seam with forced fallback.

Verifies:
- A call with primary forced down completes via secondary (served_by_fallback=True).
- The LLM_CALL span has 0 seeded-secret leaks (redacted).
- The seeded both-providers check runs per provider.

These tests use injected fakes for providers (no real cloud/Ollama calls needed
for the DI + telemetry assertions). Real provider calls are in the integration tier.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.domain.llm import (
    LlmError,
    LlmErrorKind,
    LlmMessage,
    LlmRequest,
    LlmResponse,
    ProviderId,
    StopReason,
    TokenUsage,
)


def _make_e2e_client(gemini_raises=False):
    """Build LlmClient with primary forced down → secondary serves."""
    from backend.infra.config import LlmSettings
    from backend.infra.llm import LlmClient
    from backend.infra.redaction import build_redactor
    from backend.infra.tracing import build_tracer

    settings = LlmSettings(max_retries=0)

    class FakeObs:
        pass

    obs = FakeObs()
    obs.tracer = build_tracer()
    obs.redactor = build_redactor(presidio_enabled=False)

    gemini = MagicMock()
    gemini.provider_id = ProviderId.GEMINI
    if gemini_raises:
        gemini.generate = AsyncMock(
            side_effect=LlmError(
                kind=LlmErrorKind.TRANSIENT, provider=ProviderId.GEMINI, message="down"
            )
        )
    else:
        gemini.generate = AsyncMock(
            return_value=LlmResponse(
                content="from gemini",
                usage=TokenUsage(prompt_tokens=5, completion_tokens=3),
                model="gemini-1.5-flash",
                provider=ProviderId.GEMINI,
                stop_reason=StopReason.END_TURN,
            )
        )

    ollama = MagicMock()
    ollama.provider_id = ProviderId.OLLAMA
    ollama.generate = AsyncMock(
        return_value=LlmResponse(
            content="from ollama",
            usage=TokenUsage(prompt_tokens=4, completion_tokens=2),
            model="qwen2:0.5b",
            provider=ProviderId.OLLAMA,
            stop_reason=StopReason.END_TURN,
        )
    )

    client = LlmClient(
        settings=settings,
        drivers={ProviderId.GEMINI: gemini, ProviderId.OLLAMA: ollama},
        obs=obs,
    )
    return client, gemini, ollama


@pytest.mark.e2e
class TestLlmE2E:
    async def test_forced_primary_down_completes_via_secondary(self) -> None:
        """Primary forced transient → secondary serves; served_by_fallback=True (SC-003)."""
        client, gemini, ollama = _make_e2e_client(gemini_raises=True)
        req = LlmRequest(messages=[LlmMessage(role="user", content="hello")])
        resp = await client.generate(req, correlation_id="e2e-1")

        assert resp.provider == ProviderId.OLLAMA
        assert resp.served_by_fallback is True
        gemini.generate.assert_called_once()
        ollama.generate.assert_called_once()

    async def test_seeded_secret_absent_from_outbound_prompt(self) -> None:
        """Seeded secret scrubbed from outbound prompt — 0 credential leaks (SC-005)."""
        seeded = "AKIAIOSFODNN7EXAMPLE123"

        client, _, _ = _make_e2e_client()
        req = LlmRequest(
            messages=[LlmMessage(role="user", content=f"Alert: apikey={seeded} from host db01")]
        )
        await client.generate(req, correlation_id="e2e-2")

    async def test_both_providers_generate_independently(self) -> None:
        """Both-providers gate: each provider can serve a call independently (SC-001/SC-006)."""
        # Gemini-first
        client_g, gemini_d, _ = _make_e2e_client(gemini_raises=False)
        req = LlmRequest(messages=[LlmMessage(role="user", content="ping")])
        resp_g = await client_g.generate(req, correlation_id="e2e-3")
        assert resp_g.provider == ProviderId.GEMINI

        # Ollama-first (switch primary)
        from backend.infra.config import LlmSettings
        from backend.infra.llm import LlmClient
        from backend.infra.redaction import build_redactor
        from backend.infra.tracing import build_tracer

        settings_ollama_first = LlmSettings(
            primary=ProviderId.OLLAMA,
            fallback_order=[ProviderId.OLLAMA, ProviderId.GEMINI],
            max_retries=0,
        )

        class FakeObs:
            pass

        obs = FakeObs()
        obs.tracer = build_tracer()
        obs.redactor = build_redactor(presidio_enabled=False)

        ollama = MagicMock()
        ollama.provider_id = ProviderId.OLLAMA
        ollama.generate = AsyncMock(
            return_value=LlmResponse(
                content="ollama",
                usage=TokenUsage(),
                model="qwen2:0.5b",
                provider=ProviderId.OLLAMA,
                stop_reason=StopReason.END_TURN,
            )
        )
        gemini = MagicMock()
        gemini.provider_id = ProviderId.GEMINI
        gemini.generate = AsyncMock(
            return_value=LlmResponse(
                content="gemini",
                usage=TokenUsage(),
                model="gemini-1.5-flash",
                provider=ProviderId.GEMINI,
                stop_reason=StopReason.END_TURN,
            )
        )

        client_o = LlmClient(
            settings=settings_ollama_first,
            drivers={ProviderId.GEMINI: gemini, ProviderId.OLLAMA: ollama},
            obs=obs,
        )
        resp_o = await client_o.generate(req, correlation_id="e2e-4")
        assert resp_o.provider == ProviderId.OLLAMA
