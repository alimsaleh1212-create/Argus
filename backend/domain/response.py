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
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    REGRESSED = "regressed"


# Worst-case ordering for aggregate: REGRESSED > UNVERIFIED > VERIFIED
_VERDICT_RANK: dict[VerificationVerdict, int] = {
    VerificationVerdict.VERIFIED: 0,
    VerificationVerdict.UNVERIFIED: 1,
    VerificationVerdict.REGRESSED: 2,
}


class ProbeState(StrEnum):
    """Observed executor post-state (read-only; never an efficacy claim)."""

    EXPECTED = "expected"      # control reports the intended post-state
    UNEXPECTED = "unexpected"  # control reports the threat persists / action not in effect
    INCONCLUSIVE = "inconclusive"  # control could not be read → fail-closed unverified


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


class ProbeResult(BaseModel):
    """One executor's observed post-state for one action (read-only)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: ActionType
    target: str
    state: ProbeState
    detail: str = ""


class ActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ActionType
    target: str
    status: ActionStatus
    detail: str = ""
    verification: VerificationVerdict | None = None


class IndicatorRecheck(BaseModel):
    """Current time-valid reputation for one applied target (the real data path)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target: str
    intel_verdict: Literal["benign", "malicious", "suspicious", "unknown"]
    fact_value: str | None = None
    fact_is_current: bool = False  # FactState.is_current — current vs superseded


class VerificationSignals(BaseModel):
    """Inputs to the pure verdict function, per action."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    probe: ProbeResult
    recheck: IndicatorRecheck | None = None  # None when target has no re-checkable indicator


class VerificationRecord(BaseModel):
    """Rides evidence["response"]["verification"] — the full verification audit trail."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: VerificationVerdict
    per_action: list[ActionResult]
    signals: list[VerificationSignals]
    used_llm_tiebreak: bool = False
    rationale: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Pure verdict functions (T012) — no I/O, fully unit-testable
# ---------------------------------------------------------------------------


def decide_action_verdict(signals: VerificationSignals, cfg: object) -> VerificationVerdict:
    """Compute per-action verdict from probe + indicator re-check signals.

    Rules (deterministic, worst-case; LLM tiebreak only when explicitly enabled):
    - probe UNEXPECTED (any intel) → REGRESSED
    - intel/fact in regressed set (current only) → REGRESSED
    - probe EXPECTED + intel clean (or no indicator) → VERIFIED
    - probe INCONCLUSIVE or intel unknown/absent → UNVERIFIED
    - genuine conflict (probe EXPECTED, intel in regressed set) → REGRESSED (deterministic)
    """
    regressed_set: set[str] = set(getattr(cfg, "verify_regressed_verdicts", ["malicious", "suspicious"]))

    probe_state = signals.probe.state
    recheck = signals.recheck

    # Probe says the threat persists or action not in effect → regressed unconditionally
    if probe_state == ProbeState.UNEXPECTED:
        return VerificationVerdict.REGRESSED

    # Check indicator signals (current time-valid only)
    intel_regressed = False
    intel_clean = False
    intel_unknown = True

    if recheck is not None:
        intel_unknown = False
        if recheck.intel_verdict in regressed_set:
            intel_regressed = True
        elif recheck.intel_verdict in ("benign",):
            intel_clean = True
        # else: "unknown" or other → treat as unknown

        # Current fact in regressed set also triggers regressed
        if recheck.fact_is_current and recheck.fact_value in regressed_set:
            intel_regressed = True

    if intel_regressed:
        return VerificationVerdict.REGRESSED

    if probe_state == ProbeState.INCONCLUSIVE:
        return VerificationVerdict.UNVERIFIED

    # probe is EXPECTED
    if intel_unknown:
        # No indicator to check → confirmed by probe alone
        return VerificationVerdict.VERIFIED

    if intel_clean:
        return VerificationVerdict.VERIFIED

    # intel is neither regressed nor clean (e.g. "unknown" from a non-None recheck)
    return VerificationVerdict.UNVERIFIED


def decide_verdict(
    per_action: list[VerificationSignals], cfg: object
) -> VerificationVerdict:
    """Worst-case aggregate verdict across all applied actions.

    REGRESSED > UNVERIFIED > VERIFIED. An empty list yields UNVERIFIED (fail-closed).
    """
    if not per_action:
        return VerificationVerdict.UNVERIFIED

    worst = VerificationVerdict.VERIFIED
    for sig in per_action:
        v = decide_action_verdict(sig, cfg)
        if _VERDICT_RANK[v] > _VERDICT_RANK[worst]:
            worst = v
        if worst == VerificationVerdict.REGRESSED:
            break  # can't get worse
    return worst


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
    """Protocol: each mock/real executor implements this interface.

    probe() is read-only — MUST NOT mutate the environment.
    Never raises into the caller; errors → ProbeResult(state=INCONCLUSIVE).
    """

    async def execute(self, action: RemediationAction) -> ActionResult:
        raise NotImplementedError

    async def probe(self, action: RemediationAction) -> ProbeResult:  # noqa: D102
        raise NotImplementedError
