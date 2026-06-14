"""LLM drivers — the ONLY module importing google-genai / ollama (FR-001 no-bypass).

Each driver maps the uniform LlmRequest ↔ vendor API and normalizes the vendor
response into the uniform LlmResponse. Error classification lives here too so the
fallback loop in llm.py works with LlmErrorKind — never raw vendor exceptions.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import ollama as ollama_sdk

# Vendor SDK imports — confined to this module (no-bypass FR-001)
from google import genai
from google.genai import types as genai_types

from backend.domain.llm import (
    LlmError,
    LlmErrorKind,
    LlmRequest,
    LlmResponse,
    ProviderCapability,
    ProviderId,
    StopReason,
    TokenUsage,
    ToolCall,
)

if TYPE_CHECKING:
    from backend.infra.config import LlmSettings


# ---------------------------------------------------------------------------
# Driver protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Driver(Protocol):
    provider_id: ProviderId
    capability: ProviderCapability

    async def generate(self, request: LlmRequest) -> LlmResponse: ...

    async def ping(self) -> bool:
        """Return True if the provider is reachable (used for readiness probe)."""
        ...


# ---------------------------------------------------------------------------
# Error classification helpers
# ---------------------------------------------------------------------------


def _classify_gemini_error(exc: Exception) -> LlmErrorKind:
    msg = str(exc).lower()
    exc_type = type(exc).__name__.lower()

    # Auth failures
    if any(
        k in msg
        for k in (
            "401",
            "403",
            "unauthorized",
            "forbidden",
            "api_key",
            "api key",
            "unauthenticated",
        )
    ):
        return LlmErrorKind.AUTH
    if any(
        k in exc_type for k in ("unauthorized", "forbidden", "permissiondenied", "unauthenticated")
    ):
        return LlmErrorKind.AUTH

    # Invalid request
    if any(
        k in msg for k in ("400", "invalid", "bad request", "context window", "too many tokens")
    ):
        return LlmErrorKind.INVALID_REQUEST
    if any(k in exc_type for k in ("invalidargument", "badrequest")):
        return LlmErrorKind.INVALID_REQUEST

    # Content refusal
    if any(k in msg for k in ("safety", "content filter", "policy", "refus", "harm")):
        return LlmErrorKind.CONTENT_REFUSAL

    # Transient
    if any(
        k in msg
        for k in ("429", "503", "502", "500", "rate limit", "overloaded", "unavailable", "timeout")
    ):
        return LlmErrorKind.TRANSIENT
    if any(
        k in exc_type
        for k in ("ratelimit", "serviceunavailable", "internalserver", "timeout", "deadline")
    ):
        return LlmErrorKind.TRANSIENT

    # Default: transient (retry won't hurt, and most unknown errors are transient)
    return LlmErrorKind.TRANSIENT


def _classify_ollama_error(exc: Exception) -> LlmErrorKind:
    msg = str(exc).lower()
    exc_type = type(exc).__name__.lower()

    if any(
        k in msg for k in ("connection refused", "cannot connect", "connect error", "unreachable")
    ):
        return LlmErrorKind.TRANSIENT
    if any(k in exc_type for k in ("connecterror", "timeout")):
        return LlmErrorKind.TRANSIENT
    if "model" in msg and "not found" in msg:
        return LlmErrorKind.INVALID_REQUEST

    return LlmErrorKind.TRANSIENT


def _safe_message(exc: Exception) -> str:
    """Return a secret-free error description (class + brief text)."""
    return f"{type(exc).__name__}: {str(exc)[:200]}"


# ---------------------------------------------------------------------------
# Stop-reason mapping
# ---------------------------------------------------------------------------


def _gemini_finish_reason_to_stop(name: str) -> StopReason:
    mapping = {
        "STOP": StopReason.END_TURN,
        "MAX_TOKENS": StopReason.MAX_TOKENS,
        "SAFETY": StopReason.CONTENT_FILTER,
        "RECITATION": StopReason.CONTENT_FILTER,
        "TOOL_CODE": StopReason.TOOL_USE,
        "FINISH_REASON_UNSPECIFIED": StopReason.UNKNOWN,
    }
    return mapping.get(name.upper(), StopReason.UNKNOWN)


def _ollama_done_reason_to_stop(reason: str | None) -> StopReason:
    mapping = {
        "stop": StopReason.END_TURN,
        "length": StopReason.MAX_TOKENS,
        "tool_calls": StopReason.TOOL_USE,
    }
    return mapping.get((reason or "").lower(), StopReason.UNKNOWN)


# ---------------------------------------------------------------------------
# Gemini driver
# ---------------------------------------------------------------------------


class GeminiDriver:
    provider_id: ProviderId = ProviderId.GEMINI
    capability: ProviderCapability = ProviderCapability(
        provider=ProviderId.GEMINI,
        supports_tools=True,
        supports_structured_output=True,
        reports_token_usage=True,
    )

    def __init__(self, settings: LlmSettings, api_key: str) -> None:
        self._model = settings.gemini_model
        self._client = genai.Client(api_key=api_key)

    async def generate(self, request: LlmRequest) -> LlmResponse:
        contents, system_instruction = _build_gemini_contents(request)
        config = _build_gemini_config(request, system_instruction)

        try:
            raw = await self._client.aio.models.generate_content(
                model=self._model,
                contents=contents,
                config=config,
            )
        except TimeoutError as exc:
            raise LlmError(
                kind=LlmErrorKind.TRANSIENT,
                provider=ProviderId.GEMINI,
                message="Request timed out",
            ) from exc
        except Exception as exc:
            kind = _classify_gemini_error(exc)
            raise LlmError(
                kind=kind, provider=ProviderId.GEMINI, message=_safe_message(exc)
            ) from exc

        return _parse_gemini_response(raw, self._model)

    async def ping(self) -> bool:
        try:
            await asyncio.wait_for(
                self._client.aio.models.list(),
                timeout=5.0,
            )
            return True
        except Exception:
            return False


def _build_gemini_contents(
    request: LlmRequest,
) -> tuple[list[genai_types.Content], str | None]:
    """Convert LlmMessages to Gemini Contents; extract system instruction."""
    system_instruction: str | None = request.system
    contents: list[genai_types.Content] = []

    for msg in request.messages:
        if msg.role == "system":
            # Gemini uses system_instruction in config, not as a turn
            system_instruction = (system_instruction or "") + msg.content
            continue
        if msg.role == "tool":
            # Tool result → function response Part
            part = genai_types.Part(
                function_response=genai_types.FunctionResponse(
                    name=msg.name or "unknown",
                    response={"output": msg.content},
                )
            )
            contents.append(genai_types.Content(role="user", parts=[part]))
        elif msg.role == "assistant":
            contents.append(
                genai_types.Content(role="model", parts=[genai_types.Part(text=msg.content)])
            )
        else:  # user
            contents.append(
                genai_types.Content(role="user", parts=[genai_types.Part(text=msg.content)])
            )

    return contents, system_instruction or None


def _build_gemini_config(
    request: LlmRequest, system_instruction: str | None
) -> genai_types.GenerateContentConfig:
    kwargs: dict[str, Any] = {}
    if system_instruction:
        kwargs["system_instruction"] = system_instruction
    if request.temperature is not None:
        kwargs["temperature"] = request.temperature
    if request.top_p is not None:
        kwargs["top_p"] = request.top_p
    if request.max_tokens is not None:
        kwargs["max_output_tokens"] = request.max_tokens

    # Tools
    if request.tools:
        decls = [
            genai_types.FunctionDeclaration(
                name=t.name,
                description=t.description,
                parameters=t.parameters,
            )
            for t in request.tools
        ]
        kwargs["tools"] = [genai_types.Tool(function_declarations=decls)]

        if request.require_tool:
            allowed = [request.require_tool] if isinstance(request.require_tool, str) else None
            mode = "ANY" if allowed else "AUTO"
            kwargs["tool_config"] = genai_types.ToolConfig(
                function_calling_config=genai_types.FunctionCallingConfig(
                    mode=mode,
                    **({"allowed_function_names": allowed} if allowed else {}),
                )
            )

    # Structured output
    if request.response_schema is not None:
        kwargs["response_mime_type"] = "application/json"
        kwargs["response_schema"] = request.response_schema

    return genai_types.GenerateContentConfig(**kwargs)


def _parse_gemini_response(raw: Any, model: str) -> LlmResponse:
    # Text content
    content = ""
    try:
        content = raw.text or ""
    except Exception:
        pass

    # Tool calls
    tool_calls: list[ToolCall] = []
    try:
        for fc in raw.function_calls or []:
            args = dict(fc.args) if hasattr(fc, "args") else {}
            tool_calls.append(ToolCall(id=str(id(fc)), name=fc.name, arguments=args))
    except Exception:
        pass

    # Usage
    usage = TokenUsage()
    try:
        meta = raw.usage_metadata
        usage = TokenUsage(
            prompt_tokens=getattr(meta, "prompt_token_count", None),
            completion_tokens=getattr(meta, "candidates_token_count", None),
        )
    except Exception:
        pass

    # Stop reason
    stop_reason = StopReason.UNKNOWN
    try:
        reason_name = raw.candidates[0].finish_reason.name
        stop_reason = _gemini_finish_reason_to_stop(reason_name)
        if tool_calls:
            stop_reason = StopReason.TOOL_USE
    except Exception:
        pass

    return LlmResponse(
        content=content,
        tool_calls=tool_calls,
        usage=usage,
        model=model,
        provider=ProviderId.GEMINI,
        stop_reason=stop_reason,
    )


# ---------------------------------------------------------------------------
# Ollama driver
# ---------------------------------------------------------------------------


class OllamaDriver:
    provider_id: ProviderId = ProviderId.OLLAMA
    capability: ProviderCapability = ProviderCapability(
        provider=ProviderId.OLLAMA,
        supports_tools=True,
        supports_structured_output=True,
        reports_token_usage=True,
    )

    def __init__(self, settings: LlmSettings) -> None:
        self._model = settings.ollama_model
        self._host = settings.ollama_base_url
        self._client = ollama_sdk.AsyncClient(host=self._host)

    async def generate(self, request: LlmRequest) -> LlmResponse:
        messages = _build_ollama_messages(request)
        kwargs: dict[str, Any] = {"model": self._model, "messages": messages}

        # Tools
        if request.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in request.tools
            ]

        # Structured output via format
        if request.response_schema is not None:
            kwargs["format"] = request.response_schema
        elif (
            request.require_tool and isinstance(request.require_tool, bool) and request.require_tool
        ):
            pass  # tool use handles the response structure

        # Generation options
        options: dict[str, Any] = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.top_p is not None:
            options["top_p"] = request.top_p
        if request.max_tokens is not None:
            options["num_predict"] = request.max_tokens
        if options:
            kwargs["options"] = options

        try:
            raw = await self._client.chat(**kwargs)
        except TimeoutError as exc:
            raise LlmError(
                kind=LlmErrorKind.TRANSIENT,
                provider=ProviderId.OLLAMA,
                message="Request timed out",
            ) from exc
        except Exception as exc:
            kind = _classify_ollama_error(exc)
            raise LlmError(
                kind=kind, provider=ProviderId.OLLAMA, message=_safe_message(exc)
            ) from exc

        return _parse_ollama_response(raw, self._model)

    async def ping(self) -> bool:
        try:
            await asyncio.wait_for(self._client.list(), timeout=5.0)
            return True
        except Exception:
            return False


def _build_ollama_messages(request: LlmRequest) -> list[dict]:
    messages = []
    if request.system:
        messages.append({"role": "system", "content": request.system})
    for msg in request.messages:
        if msg.role == "system":
            messages.append({"role": "system", "content": msg.content})
        elif msg.role == "tool":
            messages.append({"role": "tool", "content": msg.content, "name": msg.name or ""})
        else:
            messages.append({"role": msg.role, "content": msg.content})
    return messages


def _parse_ollama_response(raw: Any, model: str) -> LlmResponse:
    msg = raw.message if hasattr(raw, "message") else raw

    # Text content
    content = getattr(msg, "content", "") or ""

    # Tool calls
    tool_calls: list[ToolCall] = []
    raw_tcs = getattr(msg, "tool_calls", None) or []
    for i, tc in enumerate(raw_tcs):
        try:
            fn = tc.function if hasattr(tc, "function") else tc
            name = getattr(fn, "name", f"tool_{i}")
            args = getattr(fn, "arguments", {})
            if isinstance(args, str):
                args = json.loads(args)
            tool_calls.append(ToolCall(id=f"ollama-tc-{i}", name=name, arguments=dict(args)))
        except Exception:
            pass

    # Usage
    usage = TokenUsage(
        prompt_tokens=getattr(raw, "prompt_eval_count", None),
        completion_tokens=getattr(raw, "eval_count", None),
    )

    # Stop reason
    done_reason = getattr(raw, "done_reason", None)
    stop_reason = _ollama_done_reason_to_stop(done_reason)
    if tool_calls:
        stop_reason = StopReason.TOOL_USE

    return LlmResponse(
        content=content,
        tool_calls=tool_calls,
        usage=usage,
        model=model,
        provider=ProviderId.OLLAMA,
        stop_reason=stop_reason,
    )
