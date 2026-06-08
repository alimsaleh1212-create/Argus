"""Pure LLM domain types — no outward dependencies (SPEC-llm-provider #3).

These are the uniform shapes the provider-agnostic seam produces and consumes.
Nothing here is incident/business logic and nothing imports from infra or above.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ProviderId(StrEnum):
    GEMINI = "gemini"
    OLLAMA = "ollama"


class StopReason(StrEnum):
    END_TURN = "end_turn"
    MAX_TOKENS = "max_tokens"
    TOOL_USE = "tool_use"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"
    UNKNOWN = "unknown"


class LlmErrorKind(StrEnum):
    TRANSIENT = "transient"
    AUTH = "auth"
    INVALID_REQUEST = "invalid_request"
    CONTENT_REFUSAL = "content_refusal"
    CONTRACT_UNSATISFIED = "contract_unsatisfied"
    EXHAUSTED = "exhausted"


class LlmMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    name: str | None = None


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict


class LlmRequest(BaseModel):
    messages: list[LlmMessage] = Field(..., min_length=1)
    system: str | None = None
    tools: list[ToolSpec] = Field(default_factory=list)
    response_schema: dict | None = None
    require_tool: str | bool | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None

    @field_validator("messages")
    @classmethod
    def messages_nonempty(cls, v: list) -> list:
        if not v:
            raise ValueError("messages must not be empty")
        return v


class TokenUsage(BaseModel):
    """Normalized token counts — field names match #2's record_llm_usage hook (LD6)."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict


class LlmResponse(BaseModel):
    content: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: TokenUsage
    model: str
    provider: ProviderId
    stop_reason: StopReason
    served_by_fallback: bool = False


class ProviderCapability(BaseModel):
    """Used for request shaping and telemetry only — not for routing (LD4)."""

    provider: ProviderId
    supports_tools: bool
    supports_structured_output: bool
    reports_token_usage: bool


class LlmError(Exception):
    """Raised by the adapter; never a vendor exception.

    kind drives retry/fallback/surface decisions in the caller.
    message is always secret-free — names the condition, not the value.
    """

    def __init__(
        self,
        kind: LlmErrorKind,
        *,
        provider: ProviderId | None = None,
        message: str = "",
        attempts: list[ProviderId] | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.provider = provider
        self.message = message
        self.attempts: list[ProviderId] = attempts or []
