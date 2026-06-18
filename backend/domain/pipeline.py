"""Pure pipeline types — the single contract imported by #7/#8/#9/#10/#12.

No outward imports (domain-isolation import-linter contract).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

# Forward reference resolved at runtime; TYPE_CHECKING import avoided to satisfy
# the no-outward-imports domain-isolation rule — callers import Incident separately.
if False:  # noqa: SIM210
    from backend.domain.incident import Incident


class StageName(StrEnum):
    TRIAGE = "triage"
    ENRICHMENT = "enrichment"
    RESPONSE = "response"


class StageOutcome(StrEnum):
    RESOLVED = "resolved"
    ADVANCE = "advance"
    NEEDS_APPROVAL = "needs_approval"
    ESCALATE = "escalate"
    UNVERIFIED = "unverified"  # remediation applied but verdict unconfirmed → escalate


class StageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: StageName
    outcome: StageOutcome
    tokens_consumed: int = 0
    # Split token usage (prompt/completion) for trace telemetry. None when the
    # provider omits usage (FR-013); rendered as "unknown" rather than coerced.
    tokens_in: int | None = None
    tokens_out: int | None = None
    llm_model: str | None = None
    disposition: str | None = None
    evidence_patch: dict[str, Any] | None = None
    note: str | None = None


class ToolError(Exception):
    """Structured stage failure — supervisor inspects `retryable` to decide retry vs degrade."""

    def __init__(self, *, retryable: bool, kind: str, detail: str = "") -> None:
        self.retryable = retryable
        self.kind = kind
        self.detail = detail
        super().__init__(f"{kind}: {detail}" if detail else kind)


# The frozen stage-handler interface: incident slice in, StageResult out (or ToolError raised).
# TYPE_CHECKING block avoids circular import; at runtime this is just a plain Callable alias.
StageHandler = Callable[["Incident"], Awaitable[StageResult]]  # type: ignore[type-arg]
