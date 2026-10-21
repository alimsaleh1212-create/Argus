"""Pure typed-decision types (Jev, JD1) — no infra imports.

A DecisionClient answers closed-set questions about a `state` with a typed answer
(the chosen option, per-option probabilities and a calibrated confidence) instead
of generated text. Only the `choice` question type is modelled — the one Argus uses.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class DecisionErrorKind(StrEnum):
    AUTH = "auth"
    INVALID_REQUEST = "invalid_request"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    MALFORMED = "malformed"


class DecisionError(Exception):
    """Raised by a DecisionClient on any failure. Callers fall back or fail closed (JD3)."""

    def __init__(self, kind: DecisionErrorKind, message: str = "") -> None:
        super().__init__(f"{kind.value}: {message}" if message else kind.value)
        self.kind = kind


class ChoiceQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instructions: str = Field(min_length=1)
    criteria: dict[str, str] = Field(min_length=1, max_length=255)


class ChoiceAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    choice: str
    confidence: float = Field(ge=0.0, le=1.0)
    probabilities: dict[str, float] = Field(default_factory=dict)


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: str | dict[str, Any] | list[Any]
    questions: dict[str, ChoiceQuestion] = Field(min_length=1)


class DecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str
    answers: dict[str, ChoiceAnswer]
    input_tokens: int | None = None
    output_tokens: int | None = None

    @property
    def total_tokens(self) -> int:
        return (self.input_tokens or 0) + (self.output_tokens or 0)


class DecisionClient(Protocol):
    async def decide(
        self, request: DecisionRequest, *, correlation_id: str
    ) -> DecisionResponse: ...
