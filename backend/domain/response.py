"""Pure domain types for the response/remediation stage.

No outward imports — isolated like domain/triage.py / domain/enrichment.py.
Importable by the dashboard (#12) and eval without pulling infra.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ActionType(StrEnum):
    ADD_TO_WATCHLIST = "add_to_watchlist"
    OPEN_TICKET = "open_ticket"
    ENRICH_AND_TAG = "enrich_and_tag"
    ISOLATE_HOST = "isolate_host"
    DISABLE_USER = "disable_user"
    BLOCK_IP = "block_ip"


class RiskClass(StrEnum):
    AUTO = "auto"
    APPROVAL_REQUIRED = "approval_required"


class ActionStatus(StrEnum):
    APPLIED = "applied"
    FAILED = "failed"
    NOT_EXECUTED = "not_executed"


class VerificationVerdict(StrEnum):
    """RESERVED for §v2c (T2) — unused in v1."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    REGRESSED = "regressed"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class RemediationAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ActionType
    target: str
    params: dict[str, Any] = Field(default_factory=dict)
    risk: RiskClass
    idempotency_key: str


class ActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ActionType
    target: str
    status: ActionStatus
    detail: str = ""
    verification: VerificationVerdict | None = None  # RESERVED §v2c — always None in v1


class RemediationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str
    playbook_id: str
    actions: list[RemediationAction] = Field(min_length=1)
    rationale: str = Field(min_length=1)
    selected_by: Literal["deterministic", "llm"]

    @property
    def has_approval_required(self) -> bool:
        return any(a.risk == RiskClass.APPROVAL_REQUIRED for a in self.actions)


class ActionExecutor:
    """Protocol: each mock/real executor implements this interface."""

    async def execute(self, action: RemediationAction) -> ActionResult:
        raise NotImplementedError
