"""Unit tests — T020 [US3]: per-call telemetry and credential redaction.

Providers are mocked — zero real calls. Tests verify that:
- generate() opens an LLM_CALL span with provider/model/tokens/latency.
- Usage omitted → tokens stay None ("unknown" in views).
- A seeded credential in the prompt is scrubbed before transmission.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from backend.domain.llm import (
    LlmMessage,
    LlmRequest,
    LlmResponse,
    ProviderId,
    StopReason,
    TokenUsage,
)


def _build_client_with_recording_tracer(fake_response=None, raise_error=None):
    """Build a client whose tracer records spans in memory for inspection."""
    from backend.infra.config import LlmSettings
    from backend.infra.llm import LlmClient
    from backend.infra.redaction import build_redactor
    from backend.infra.tracing import build_tracer

    settings = LlmSettings(max_retries=0)
    tracer = build_tracer()  # No exporter — spans go nowhere but we can inspect via the span cm

    redactor = build_redactor(presidio_enabled=False)

    class FakeObs:
        pass

    obs = FakeObs()
    obs.tracer = tracer
    obs.redactor = redactor

    driver = MagicMock()
    driver.provider_id = ProviderId.GEMINI
    if raise_error:
        driver.generate = AsyncMock(side_effect=raise_error)
    else:
        resp = fake_response or LlmResponse(
            content="pong",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
            model="gemini-1.5-flash",
            provider=ProviderId.GEMINI,
            stop_reason=StopReason.END_TURN,
        )
        driver.generate = AsyncMock(return_value=resp)

    client = LlmClient(
        settings=settings, drivers={ProviderId.GEMINI: driver, ProviderId.OLLAMA: driver}, obs=obs
    )
    return client, driver, obs


class TestTelemetrySpan:
    async def test_generate_opens_llm_call_span(self) -> None:
        """generate() opens an LLM_CALL span (FR-011)."""
        from backend.domain.telemetry import SpanKind

        recorded_spans = []

        from backend.infra import tracing as tracing_mod

        original_queue = tracing_mod._Tracer._queue_span

        def capturing_queue(self_tracer, span):
            recorded_spans.append(span)

        tracing_mod._Tracer._queue_span = capturing_queue

        try:
            client, driver, _ = _build_client_with_recording_tracer()
            req = LlmRequest(messages=[LlmMessage(role="user", content="ping")])
            await client.generate(req, correlation_id="span-test-1")
        finally:
            tracing_mod._Tracer._queue_span = original_queue

        assert len(recorded_spans) >= 1
        llm_span = next((s for s in recorded_spans if s.kind == SpanKind.LLM_CALL), None)
        assert llm_span is not None, "No LLM_CALL span was recorded"

    async def test_span_carries_provider_model_tokens_latency(self) -> None:
        """LLM_CALL span has provider, model, tokens_in/out, and latency (SC-004)."""
        from backend.domain.telemetry import SpanKind

        recorded_spans = []
        from backend.infra import tracing as tracing_mod

        original_queue = tracing_mod._Tracer._queue_span

        def capturing_queue(self_tracer, span):
            recorded_spans.append(span)

        tracing_mod._Tracer._queue_span = capturing_queue

        try:
            client, driver, _ = _build_client_with_recording_tracer()
            req = LlmRequest(messages=[LlmMessage(role="user", content="ping")])
            await client.generate(req, correlation_id="span-test-2")
        finally:
            tracing_mod._Tracer._queue_span = original_queue

        llm_span = next(s for s in recorded_spans if s.kind == SpanKind.LLM_CALL)
        assert llm_span.llm_model == "gemini-1.5-flash"
        assert llm_span.tokens_in == 10
        assert llm_span.tokens_out == 5
        assert llm_span.latency_ms is not None and llm_span.latency_ms >= 0

    async def test_usage_omitted_tokens_stay_none(self) -> None:
        """When provider omits usage, span tokens remain None (rendered as 'unknown')."""
        from backend.domain.telemetry import SpanKind

        resp_no_usage = LlmResponse(
            content="ok",
            usage=TokenUsage(prompt_tokens=None, completion_tokens=None),
            model="gemini-1.5-flash",
            provider=ProviderId.GEMINI,
            stop_reason=StopReason.END_TURN,
        )

        recorded_spans = []
        from backend.infra import tracing as tracing_mod

        original_queue = tracing_mod._Tracer._queue_span

        def capturing_queue(self_tracer, span):
            recorded_spans.append(span)

        tracing_mod._Tracer._queue_span = capturing_queue

        try:
            client, _, _ = _build_client_with_recording_tracer(fake_response=resp_no_usage)
            req = LlmRequest(messages=[LlmMessage(role="user", content="ping")])
            await client.generate(req, correlation_id="span-test-3")
        finally:
            tracing_mod._Tracer._queue_span = original_queue

        llm_span = next(s for s in recorded_spans if s.kind == SpanKind.LLM_CALL)
        assert llm_span.tokens_in is None
        assert llm_span.tokens_out is None


class TestCredentialScrubbing:
    async def test_credential_in_prompt_scrubbed_from_outbound_request(self) -> None:
        """A seeded credential in the prompt is scrubbed before transmission (FR-012 / SC-005)."""
        seeded_key = "AKIAIOSFODNN7EXAMPLE123"  # fake AWS-style key pattern

        client, driver, _ = _build_client_with_recording_tracer()
        req = LlmRequest(
            messages=[LlmMessage(role="user", content=f"Alert from host: secret={seeded_key}")]
        )
        await client.generate(req, correlation_id="cred-test-1")

        # The content forwarded to the driver should NOT contain the raw credential
        forwarded_req = driver.generate.call_args[0][0]
        for msg in forwarded_req.messages:
            assert seeded_key not in msg.content, (
                f"Raw credential found in outbound request message: {msg.content}"
            )

    async def test_operational_identifier_preserved_in_outbound_prompt(self) -> None:
        """Operational identifiers (IP, hostname) are preserved in the outbound prompt (LD7)."""
        client, driver, _ = _build_client_with_recording_tracer()
        req = LlmRequest(
            messages=[LlmMessage(role="user", content="Alert from host 10.0.0.1 — investigate")]
        )
        await client.generate(req, correlation_id="cred-test-2")

        forwarded_req = driver.generate.call_args[0][0]
        full_content = " ".join(msg.content for msg in forwarded_req.messages)
        # IP address should still be present (it's an operational identifier, not a credential)
        assert "10.0.0.1" in full_content
