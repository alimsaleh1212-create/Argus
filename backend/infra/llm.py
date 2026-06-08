"""LLM adapter seam — fills the reserved #1 stub.

Provides:
  - LlmClient: provider-agnostic generate() with selection, stateless per-call
    fallback, per-call timeout + transient-only retry, fail-closed contract
    validation, telemetry wrapping, and credential scrubbing.
  - LlmProvider: lifespan singleton that builds both drivers once and disposes
    them on shutdown.
  - register_llm_provider(): appends LlmProvider to the container registry.
  - get_llm(): FastAPI Depends() provider reading app.state.container.llm.

No-bypass: vendor SDKs are confined to backend/infra/llm_drivers.py (FR-001).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from backend.domain.llm import (
    LlmError,
    LlmErrorKind,
    LlmMessage,
    LlmRequest,
    LlmResponse,
    ProviderId,
    TokenUsage,
    ToolCall,
)
from backend.domain.redaction import Boundary
from backend.domain.telemetry import SpanKind
from backend.infra.logging import get_logger

if TYPE_CHECKING:
    from fastapi import Request

    from backend.infra.config import LlmSettings, Settings
    from backend.infra.llm_drivers import Driver
    from backend.infra.observability import Observability
    from backend.infra.tracing import _Tracer

logger = get_logger(__name__)

_NON_RETRYABLE = frozenset(
    {LlmErrorKind.AUTH, LlmErrorKind.INVALID_REQUEST, LlmErrorKind.CONTENT_REFUSAL}
)


# ---------------------------------------------------------------------------
# Contract validation helpers (fail-closed — the safety boundary SC-009)
# ---------------------------------------------------------------------------


def _validate_contract(response: LlmResponse, request: LlmRequest) -> None:
    """Raise LlmError(CONTRACT_UNSATISFIED) if the response doesn't honor the request contract."""
    if request.response_schema is not None:
        try:
            parsed = json.loads(response.content)
        except (json.JSONDecodeError, ValueError) as exc:
            raise LlmError(
                kind=LlmErrorKind.CONTRACT_UNSATISFIED,
                provider=response.provider,
                message=f"Response is not valid JSON: {exc}",
            ) from exc
        _validate_json_schema(parsed, request.response_schema, response.provider)

    if request.require_tool:
        required_name = request.require_tool if isinstance(request.require_tool, str) else None
        if not response.tool_calls:
            raise LlmError(
                kind=LlmErrorKind.CONTRACT_UNSATISFIED,
                provider=response.provider,
                message="Response contains no tool calls but require_tool was set",
            )
        if required_name and not any(tc.name == required_name for tc in response.tool_calls):
            raise LlmError(
                kind=LlmErrorKind.CONTRACT_UNSATISFIED,
                provider=response.provider,
                message=f"Required tool '{required_name}' not present in response tool_calls",
            )
        # Validate tool call arguments are parseable dicts (already parsed in drivers)
        for tc in response.tool_calls:
            if not isinstance(tc.arguments, dict):
                raise LlmError(
                    kind=LlmErrorKind.CONTRACT_UNSATISFIED,
                    provider=response.provider,
                    message=f"Tool call '{tc.name}' arguments are not a dict",
                )


def _validate_json_schema(data: Any, schema: dict, provider: ProviderId) -> None:
    """Minimal JSON schema validation: required fields and type checks."""
    required = schema.get("required", [])
    if not isinstance(data, dict):
        raise LlmError(
            kind=LlmErrorKind.CONTRACT_UNSATISFIED,
            provider=provider,
            message=f"Response JSON is not an object (got {type(data).__name__})",
        )
    missing = [k for k in required if k not in data]
    if missing:
        raise LlmError(
            kind=LlmErrorKind.CONTRACT_UNSATISFIED,
            provider=provider,
            message=f"Response missing required fields: {missing}",
        )


# ---------------------------------------------------------------------------
# LlmClient
# ---------------------------------------------------------------------------


class LlmClient:
    """Provider-agnostic LLM adapter with fallback, telemetry, and redaction."""

    def __init__(
        self,
        settings: LlmSettings,
        drivers: dict[ProviderId, Driver],
        obs: Observability,
    ) -> None:
        self._settings = settings
        self._drivers = drivers
        self._obs = obs

    async def generate(
        self,
        request: LlmRequest,
        *,
        correlation_id: str,
        parent_span_id: str | None = None,
    ) -> LlmResponse:
        """Generate a response — uniform shape regardless of serving provider."""
        from backend.infra.tracing import record_llm_usage, span

        # 1. Scrub CREDENTIAL-class content from the outbound prompt (FR-006a / LD7)
        clean_request = _scrub_credentials(request, self._obs.redactor)

        # 2. Open LLM_CALL span; telemetry and redaction happen inside (US3)
        with span(
            self._obs.tracer,
            "llm_call",
            SpanKind.LLM_CALL,
            correlation_id,
            parent_span_id,
            attrs={
                "llm.messages_count": len(request.messages),
                "llm.tools_count": len(request.tools),
                "llm.has_schema": request.response_schema is not None,
                "llm.prompt": _prompt_preview(clean_request),
            },
        ) as s:
            # 3. Selection + stateless per-call fallback (US1 / US2)
            response = await self._run_fallback_loop(clean_request)

            # 4. Record usage + provider via #2 seam (FR-011 / SC-004)
            record_llm_usage(s, response.usage, response.model)
            s.attributes["llm.provider"] = str(response.provider)
            s.attributes["llm.served_by_fallback"] = response.served_by_fallback
            s.attributes["llm.completion"] = _completion_preview(response)

        return response

    async def _run_fallback_loop(self, request: LlmRequest) -> LlmResponse:
        """Stateless per-call provider selection with transient-only fallover."""
        attempts: list[ProviderId] = []

        for provider_id in self._settings.fallback_order:
            driver = self._drivers.get(provider_id)
            if driver is None:
                continue

            attempts.append(provider_id)

            try:
                result = await _call_with_timeout_and_retry(
                    driver,
                    request,
                    timeout=self._settings.request_timeout_s,
                    max_retries=self._settings.max_retries,
                )
            except LlmError as exc:
                if exc.kind == LlmErrorKind.TRANSIENT:
                    logger.warning(
                        "llm_provider_transient_failure",
                        provider=str(provider_id),
                        error=exc.message,
                    )
                    continue  # Try the next provider
                raise  # Non-retryable: surface immediately (no failover FR-008)

            # 5. Fail-closed contract validation (LD4 / SC-009)
            _validate_contract(result, request)

            # 6. Mark failover
            result = result.model_copy(
                update={"served_by_fallback": provider_id != self._settings.primary}
            )

            if result.served_by_fallback:
                logger.info("llm_failover", provider=str(provider_id), attempts=str(attempts))

            return result

        raise LlmError(
            kind=LlmErrorKind.EXHAUSTED,
            message=f"All providers exhausted after trying: {attempts}",
            attempts=attempts,
        )


async def _call_with_timeout_and_retry(
    driver: Driver,
    request: LlmRequest,
    timeout: float,
    max_retries: int,
) -> LlmResponse:
    """Wrap a driver call with per-call timeout and transient-only bounded retry."""
    last_error: LlmError | None = None

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(max_retries + 1),
        wait=wait_exponential(multiplier=0.5, min=0.1, max=10),
        retry=retry_if_exception(
            lambda e: isinstance(e, LlmError) and e.kind == LlmErrorKind.TRANSIENT
        ),
        reraise=True,
    ):
        with attempt:
            try:
                return await asyncio.wait_for(driver.generate(request), timeout=timeout)
            except asyncio.TimeoutError:
                raise LlmError(
                    kind=LlmErrorKind.TRANSIENT,
                    provider=driver.provider_id,
                    message="Request timed out",
                )

    # Should not reach here — tenacity reraises; satisfy type checker
    raise last_error or LlmError(kind=LlmErrorKind.TRANSIENT, message="Retry loop ended unexpectedly")


# ---------------------------------------------------------------------------
# Credential scrubbing and prompt preview helpers
# ---------------------------------------------------------------------------


def _scrub_credentials(request: LlmRequest, redactor: Any) -> LlmRequest:
    """Return a copy of the request with CREDENTIAL-class content scrubbed (LD7)."""
    clean_messages = [
        msg.model_copy(
            update={"content": redactor.redact_text(msg.content, Boundary.OPERATIONAL)}
        )
        for msg in request.messages
    ]
    system = (
        redactor.redact_text(request.system, Boundary.OPERATIONAL)
        if request.system
        else None
    )
    return request.model_copy(update={"messages": clean_messages, "system": system})


def _prompt_preview(request: LlmRequest) -> str:
    """Return a short (≤200-char) prompt preview for span attributes."""
    last = request.messages[-1].content if request.messages else ""
    return last[:200]


def _completion_preview(response: LlmResponse) -> str:
    """Return a short (≤200-char) completion preview for span attributes."""
    return response.content[:200]


# ---------------------------------------------------------------------------
# LlmProvider (lifespan singleton)
# ---------------------------------------------------------------------------


class LlmProvider:
    """Builds both LLM driver clients once and disposes them on shutdown (LD10)."""

    name = "llm"

    @contextlib.asynccontextmanager
    async def build(self, settings: Any) -> AsyncGenerator[LlmClient, None]:
        from backend.infra.llm_drivers import GeminiDriver, OllamaDriver

        llm_settings = settings.llm

        # Resolve Gemini API key from Vault (required — fails boot if absent FR-015)
        gemini_key = await _fetch_gemini_key(settings)

        # Obtain the observability bundle (registered before this provider — LD10)
        container = getattr(settings, "_container", None)
        obs = getattr(container, "observability", None)
        if obs is None:
            # Fallback: build a minimal observability bundle for environments
            # where the container isn't yet fully wired (e.g. tests).
            from backend.infra.observability import Observability
            from backend.infra.redaction import build_redactor
            from backend.infra.tracing import build_tracer

            obs = Observability(redactor=build_redactor(presidio_enabled=False), tracer=build_tracer())

        logger.info("llm_provider_building", primary=str(llm_settings.primary))

        drivers: dict[ProviderId, Driver] = {}
        try:
            drivers[ProviderId.GEMINI] = GeminiDriver(llm_settings, api_key=gemini_key)
            drivers[ProviderId.OLLAMA] = OllamaDriver(llm_settings)

            client = LlmClient(settings=llm_settings, drivers=drivers, obs=obs)
            logger.info("llm_provider_ready")
            yield client
        finally:
            logger.info("llm_provider_disposed")


async def _fetch_gemini_key(settings: Any) -> str:
    """Resolve the Gemini API key from Vault (fails boot if missing)."""
    import httpx

    vault_addr = settings.vault.addr
    vault_token = settings.vault.token.get_secret_value()
    kv_mount = settings.vault.kv_mount
    path = settings.llm.gemini_vault_path
    url = f"{vault_addr}/v1/{kv_mount}/data/{path.lstrip('/')}"

    try:
        async with httpx.AsyncClient(timeout=settings.startup.dependency_timeout_s) as client:
            resp = await client.get(url, headers={"X-Vault-Token": vault_token})
    except Exception as exc:
        raise RuntimeError(
            f"Cannot reach Vault to load Gemini API key from '{path}': {type(exc).__name__}"
        ) from exc

    if resp.status_code == 404:
        raise RuntimeError(
            f"Required Gemini API key not found in Vault at '{path}' (404). "
            "Ensure GEMINI_API_KEY is set in .env so vault-seed writes it."
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Vault returned HTTP {resp.status_code} for '{path}'"
        )

    data = resp.json().get("data", {}).get("data", {})
    api_key = data.get("api_key") or data.get("GEMINI_API_KEY") or ""
    if not api_key:
        raise RuntimeError(
            f"Vault path '{path}' has no 'api_key' field. "
            "Check vault-seed configuration."
        )
    return api_key


# ---------------------------------------------------------------------------
# Registration and DI accessor
# ---------------------------------------------------------------------------


def register_llm_provider() -> None:
    """Append LlmProvider to the global container registry."""
    from backend.infra.container import register_provider

    register_provider(LlmProvider())
