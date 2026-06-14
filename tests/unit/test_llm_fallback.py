"""Unit tests — T016 [US2]: stateless per-call fallback, error taxonomy, fail-closed.

All tests use injected driver fakes — zero real provider calls (SC-008).
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


def _fake_response(
    provider: ProviderId = ProviderId.GEMINI,
    content: str = "ok",
) -> LlmResponse:
    return LlmResponse(
        content=content,
        usage=TokenUsage(prompt_tokens=5, completion_tokens=3),
        model="fake-model",
        provider=provider,
        stop_reason=StopReason.END_TURN,
    )


def _driver(provider_id: ProviderId, response=None, error=None):
    d = MagicMock()
    d.provider_id = provider_id
    if error:
        d.generate = AsyncMock(side_effect=error)
    else:
        d.generate = AsyncMock(return_value=response or _fake_response(provider=provider_id))
    return d


def _make_client(gemini_driver, ollama_driver, primary=ProviderId.GEMINI, fallback_order=None):
    from backend.infra.config import LlmSettings
    from backend.infra.llm import LlmClient
    from backend.infra.redaction import build_redactor
    from backend.infra.tracing import build_tracer

    fallback_order = fallback_order or [ProviderId.GEMINI, ProviderId.OLLAMA]
    settings = LlmSettings(
        primary=primary,
        fallback_order=fallback_order,
        request_timeout_s=5.0,
        max_retries=0,  # No retry in these tests; test retry separately
    )

    class FakeObs:
        pass

    obs = FakeObs()
    obs.tracer = build_tracer()
    obs.redactor = build_redactor(presidio_enabled=False)

    client = LlmClient(
        settings=settings,
        drivers={ProviderId.GEMINI: gemini_driver, ProviderId.OLLAMA: ollama_driver},
        obs=obs,
    )
    return client


class TestStatelessFallback:
    async def test_transient_primary_fails_over_to_secondary(self) -> None:
        """Transient error on primary → next provider serves (FR-007 / SC-003)."""
        gemini = _driver(
            ProviderId.GEMINI,
            error=LlmError(
                kind=LlmErrorKind.TRANSIENT, provider=ProviderId.GEMINI, message="timeout"
            ),
        )
        ollama = _driver(ProviderId.OLLAMA, response=_fake_response(provider=ProviderId.OLLAMA))
        client = _make_client(gemini, ollama)

        req = LlmRequest(messages=[LlmMessage(role="user", content="hello")])
        resp = await client.generate(req, correlation_id="t1")

        assert resp.provider == ProviderId.OLLAMA
        assert resp.served_by_fallback is True

    async def test_every_call_starts_at_primary(self) -> None:
        """Each call begins at primary regardless of previous call result (FR-007, stateless)."""
        gemini = _driver(ProviderId.GEMINI)
        ollama = _driver(ProviderId.OLLAMA)
        client = _make_client(gemini, ollama)
        req = LlmRequest(messages=[LlmMessage(role="user", content="hi")])

        for _ in range(3):
            resp = await client.generate(req, correlation_id="t2")
            assert resp.provider == ProviderId.GEMINI
            assert resp.served_by_fallback is False

    async def test_non_retryable_auth_error_surfaces_immediately(self) -> None:
        """AUTH error on primary surfaces immediately — no failover (FR-008)."""
        gemini = _driver(
            ProviderId.GEMINI,
            error=LlmError(kind=LlmErrorKind.AUTH, provider=ProviderId.GEMINI, message="bad key"),
        )
        ollama = _driver(ProviderId.OLLAMA)
        client = _make_client(gemini, ollama)

        req = LlmRequest(messages=[LlmMessage(role="user", content="hello")])
        with pytest.raises(LlmError) as exc_info:
            await client.generate(req, correlation_id="t3")
        assert exc_info.value.kind == LlmErrorKind.AUTH
        ollama.generate.assert_not_called()

    async def test_non_retryable_invalid_request_surfaces_immediately(self) -> None:
        """INVALID_REQUEST error surfaces immediately — no failover (FR-008)."""
        gemini = _driver(
            ProviderId.GEMINI,
            error=LlmError(
                kind=LlmErrorKind.INVALID_REQUEST, provider=ProviderId.GEMINI, message="too long"
            ),
        )
        ollama = _driver(ProviderId.OLLAMA)
        client = _make_client(gemini, ollama)

        req = LlmRequest(messages=[LlmMessage(role="user", content="hello")])
        with pytest.raises(LlmError) as exc_info:
            await client.generate(req, correlation_id="t4")
        assert exc_info.value.kind == LlmErrorKind.INVALID_REQUEST

    async def test_content_refusal_surfaces_immediately(self) -> None:
        """CONTENT_REFUSAL surfaces immediately — branchable, no failover (FR-008)."""
        gemini = _driver(
            ProviderId.GEMINI,
            error=LlmError(
                kind=LlmErrorKind.CONTENT_REFUSAL, provider=ProviderId.GEMINI, message="refused"
            ),
        )
        ollama = _driver(ProviderId.OLLAMA)
        client = _make_client(gemini, ollama)

        req = LlmRequest(messages=[LlmMessage(role="user", content="hello")])
        with pytest.raises(LlmError) as exc_info:
            await client.generate(req, correlation_id="t5")
        assert exc_info.value.kind == LlmErrorKind.CONTENT_REFUSAL

    async def test_both_providers_transient_raises_exhausted(self) -> None:
        """All transient failures → single LlmError(EXHAUSTED) (FR-010)."""
        gemini = _driver(
            ProviderId.GEMINI,
            error=LlmError(kind=LlmErrorKind.TRANSIENT, provider=ProviderId.GEMINI),
        )
        ollama = _driver(
            ProviderId.OLLAMA,
            error=LlmError(kind=LlmErrorKind.TRANSIENT, provider=ProviderId.OLLAMA),
        )
        client = _make_client(gemini, ollama)

        req = LlmRequest(messages=[LlmMessage(role="user", content="hello")])
        with pytest.raises(LlmError) as exc_info:
            await client.generate(req, correlation_id="t6")
        assert exc_info.value.kind == LlmErrorKind.EXHAUSTED
        assert ProviderId.GEMINI in exc_info.value.attempts
        assert ProviderId.OLLAMA in exc_info.value.attempts

    async def test_switching_primary_flips_order_with_no_code_change(self) -> None:
        """Changing primary/fallback_order in config flips attempt order (SC-002)."""
        gemini = _driver(ProviderId.GEMINI)
        ollama = _driver(ProviderId.OLLAMA)

        # Ollama-first config
        client = _make_client(
            gemini,
            ollama,
            primary=ProviderId.OLLAMA,
            fallback_order=[ProviderId.OLLAMA, ProviderId.GEMINI],
        )
        req = LlmRequest(messages=[LlmMessage(role="user", content="hello")])
        resp = await client.generate(req, correlation_id="t7")
        assert resp.provider == ProviderId.OLLAMA
        assert resp.served_by_fallback is False
        gemini.generate.assert_not_called()

    async def test_served_by_fallback_true_when_secondary_serves(self) -> None:
        """served_by_fallback is True when a non-primary provider answers."""
        gemini = _driver(
            ProviderId.GEMINI,
            error=LlmError(kind=LlmErrorKind.TRANSIENT, provider=ProviderId.GEMINI),
        )
        ollama = _driver(ProviderId.OLLAMA)
        client = _make_client(gemini, ollama)
        req = LlmRequest(messages=[LlmMessage(role="user", content="hello")])
        resp = await client.generate(req, correlation_id="t8")
        assert resp.served_by_fallback is True


class TestFailClosed:
    async def test_failover_result_fails_schema_raises_contract_unsatisfied(self) -> None:
        """A failover result that doesn't validate against response_schema raises CONTRACT_UNSATISFIED (SC-009)."""
        import json

        schema = {"type": "object", "required": ["verdict"]}
        # Gemini fails transiently; Ollama returns content that doesn't match schema
        bad_content = json.dumps({"other": "value"})
        gemini = _driver(
            ProviderId.GEMINI,
            error=LlmError(kind=LlmErrorKind.TRANSIENT, provider=ProviderId.GEMINI),
        )
        ollama = _driver(
            ProviderId.OLLAMA,
            response=LlmResponse(
                content=bad_content,
                usage=TokenUsage(),
                model="qwen2:0.5b",
                provider=ProviderId.OLLAMA,
                stop_reason=StopReason.END_TURN,
            ),
        )
        client = _make_client(gemini, ollama)
        req = LlmRequest(
            messages=[LlmMessage(role="user", content="classify")],
            response_schema=schema,
        )
        with pytest.raises(LlmError) as exc_info:
            await client.generate(req, correlation_id="t9")
        assert exc_info.value.kind == LlmErrorKind.CONTRACT_UNSATISFIED

    async def test_require_tool_missing_raises_contract_unsatisfied(self) -> None:
        """require_tool set but not returned → CONTRACT_UNSATISFIED (SC-009)."""
        from backend.domain.llm import ToolSpec

        tool = ToolSpec(name="search", description="...", parameters={})
        gemini = _driver(
            ProviderId.GEMINI,
            response=LlmResponse(
                content="sure",
                usage=TokenUsage(),
                model="gemini",
                provider=ProviderId.GEMINI,
                stop_reason=StopReason.END_TURN,
                tool_calls=[],  # No tool call despite require_tool
            ),
        )
        ollama = _driver(ProviderId.OLLAMA)
        client = _make_client(gemini, ollama)
        req = LlmRequest(
            messages=[LlmMessage(role="user", content="search")],
            tools=[tool],
            require_tool="search",
        )
        with pytest.raises(LlmError) as exc_info:
            await client.generate(req, correlation_id="t10")
        assert exc_info.value.kind == LlmErrorKind.CONTRACT_UNSATISFIED


class TestDriverMap:
    async def test_provider_in_fallback_order_but_not_in_driver_map_is_skipped(self) -> None:
        """A provider_id in fallback_order with no matching driver is silently skipped."""
        from backend.infra.config import LlmSettings
        from backend.infra.llm import LlmClient
        from backend.infra.redaction import build_redactor
        from backend.infra.tracing import build_tracer

        ollama = _driver(ProviderId.OLLAMA, response=_fake_response(provider=ProviderId.OLLAMA))

        settings = LlmSettings(
            primary=ProviderId.GEMINI,
            fallback_order=[ProviderId.GEMINI, ProviderId.OLLAMA],
            max_retries=0,
        )

        class FakeObs:
            pass

        obs = FakeObs()
        obs.tracer = build_tracer()
        obs.redactor = build_redactor(presidio_enabled=False)

        # Gemini driver intentionally absent from map → fallback_order skips it
        client = LlmClient(
            settings=settings,
            drivers={ProviderId.OLLAMA: ollama},  # No GEMINI entry
            obs=obs,
        )
        req = LlmRequest(messages=[LlmMessage(role="user", content="hello")])
        resp = await client.generate(req, correlation_id="map-1")

        assert resp.provider == ProviderId.OLLAMA
        assert resp.served_by_fallback is True


class TestTimeout:
    async def test_timeout_treated_as_transient_and_fails_over(self) -> None:
        """asyncio.TimeoutError on driver call is caught and converted to TRANSIENT → failover (FR-009)."""
        import asyncio

        async def slow_generate(_req):
            await asyncio.sleep(60)  # will be cancelled by wait_for

        gemini = MagicMock()
        gemini.provider_id = ProviderId.GEMINI
        gemini.generate = slow_generate

        ollama = _driver(ProviderId.OLLAMA, response=_fake_response(provider=ProviderId.OLLAMA))

        from backend.infra.config import LlmSettings
        from backend.infra.llm import LlmClient
        from backend.infra.redaction import build_redactor
        from backend.infra.tracing import build_tracer

        settings = LlmSettings(
            primary=ProviderId.GEMINI,
            fallback_order=[ProviderId.GEMINI, ProviderId.OLLAMA],
            request_timeout_s=0.05,  # 50ms — triggers timeout fast
            max_retries=0,
        )

        class FakeObs:
            pass

        obs = FakeObs()
        obs.tracer = build_tracer()
        obs.redactor = build_redactor(presidio_enabled=False)

        client = LlmClient(
            settings=settings,
            drivers={ProviderId.GEMINI: gemini, ProviderId.OLLAMA: ollama},
            obs=obs,
        )

        req = LlmRequest(messages=[LlmMessage(role="user", content="hello")])
        resp = await client.generate(req, correlation_id="timeout-1")

        assert resp.provider == ProviderId.OLLAMA
        assert resp.served_by_fallback is True

    async def test_timeout_both_providers_raises_exhausted(self) -> None:
        """Timeout on all providers → EXHAUSTED (not a raw TimeoutError)."""
        import asyncio

        async def slow(_req):
            await asyncio.sleep(60)

        from backend.infra.config import LlmSettings
        from backend.infra.llm import LlmClient
        from backend.infra.redaction import build_redactor
        from backend.infra.tracing import build_tracer

        gemini = MagicMock()
        gemini.provider_id = ProviderId.GEMINI
        gemini.generate = slow

        ollama = MagicMock()
        ollama.provider_id = ProviderId.OLLAMA
        ollama.generate = slow

        settings = LlmSettings(
            request_timeout_s=0.05,
            max_retries=0,
        )

        class FakeObs:
            pass

        obs = FakeObs()
        obs.tracer = build_tracer()
        obs.redactor = build_redactor(presidio_enabled=False)

        client = LlmClient(
            settings=settings,
            drivers={ProviderId.GEMINI: gemini, ProviderId.OLLAMA: ollama},
            obs=obs,
        )

        req = LlmRequest(messages=[LlmMessage(role="user", content="hello")])
        with pytest.raises(LlmError) as exc_info:
            await client.generate(req, correlation_id="timeout-2")
        assert exc_info.value.kind == LlmErrorKind.EXHAUSTED
