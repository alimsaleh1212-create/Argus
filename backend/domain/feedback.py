"""Pure domain types and deterministic bias rules for the memory feedback loop (#16).

No outward imports — only domain→domain (Severity from backend.domain.incident).
All bias is config-backed, pure, and fully unit-testable (Constitution IV).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from backend.domain.incident import Severity


class RemediationOutcome(StrEnum):
    """Outcome values produced by verification (#15). Mirrors VerificationVerdict values."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    REGRESSED = "regressed"


FAILURE_CLASS: frozenset[RemediationOutcome] = frozenset(
    {RemediationOutcome.UNVERIFIED, RemediationOutcome.REGRESSED}
)


class FeedbackSignal(BaseModel):
    """One current, time-valid remediation-outcome fact for a single indicator."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    indicator: str
    outcome: RemediationOutcome
    is_current: bool
    observed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Pure bias rules (no I/O, no LLM)
# ---------------------------------------------------------------------------


def has_prior_failure(signals: list[FeedbackSignal], cfg: object) -> bool:
    """True iff any current signal's outcome is in cfg.feedback.escalate_on."""
    escalate_on: set[str] = set(getattr(cfg, "escalate_on", ["regressed", "unverified"]))
    return any(
        s.is_current and s.outcome.value in escalate_on for s in signals
    )


def decide_severity_bias(severity: Severity, signals: list[FeedbackSignal], cfg: object) -> Severity:
    """Raise effective severity when a current failure-class outcome exists.

    Config values:
      - "bump_one": one level up (capped at critical)
      - "to_critical": set to critical
      - "none": no change

    Idempotent: applying the same bias twice yields the same result.
    """
    bias_mode: str = getattr(cfg, "severity_bias", "bump_one")
    if bias_mode == "none" or not has_prior_failure(signals, cfg):
        return severity

    if bias_mode == "to_critical":
        return Severity.CRITICAL

    # bump_one
    order = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
    try:
        idx = order.index(severity)
    except ValueError:
        return severity
    return order[min(idx + 1, len(order) - 1)]


def prefer_stronger_playbook(
    candidates: list[object],
    signals: list[FeedbackSignal],
    cfg: object,
) -> object | None:
    """When a current failure-class outcome exists, return the highest-strength candidate.

    Returns None when:
      - feedback.prefer_stronger_playbook is disabled
      - no failure-class signal is current
      - no candidate has a higher strength than the others
      - candidates is empty

    Pure and deterministic; called before any ambiguous-tail LLM call.
    """
    enabled: bool = getattr(cfg, "prefer_stronger_playbook", True)
    if not enabled or not has_prior_failure(signals, cfg) or not candidates:
        return None

    def _strength(pb: object) -> int:
        return int(getattr(pb, "strength", 0) or 0)

    if len(candidates) <= 1:
        return None

    sorted_candidates = sorted(candidates, key=_strength, reverse=True)
    strongest = sorted_candidates[0]
    # If all candidates tie on strength, no stronger playbook exists.
    if _strength(strongest) == _strength(sorted_candidates[-1]):
        return None
    return strongest
