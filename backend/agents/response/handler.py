"""Response stage orchestration — the StageHandler factory and its two passes.

This is the ONLY stage injected with action executors (Constitution III).
  Pass A (forward): select → classify → execute auto / park destructive → verify.
  Pass B (resume):  execute the already-approved plan (no LLM) → verify.

Both passes share `_finalize_with_verification`, which runs the deterministic verification tail
(idempotent + fail-closed) and maps the verdict onto the terminal StageResult. `ApprovalRepository`
and `AuditRepository` are imported here so tests can patch them at
`backend.agents.response.handler.*`.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from backend.agents.response._log import get_logger
from backend.agents.response.catalog import PlaybookCatalog
from backend.agents.response.execution import _execute_with_audit
from backend.agents.response.selection import classify, select_playbook
from backend.agents.response.verification import verify_remediation
from backend.domain.incident import Incident
from backend.domain.pipeline import StageName, StageOutcome, StageResult
from backend.domain.response import (
    ActionExecutor,
    ActionResult,
    ActionStatus,
    ActionType,
    RemediationAction,
    RiskClass,
    VerificationRecord,
    VerificationVerdict,
)
from backend.repositories.approvals import ApprovalRepository
from backend.repositories.audit import AuditRepository

_logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Handler factory (DI by closure — Constitution III)
# ---------------------------------------------------------------------------


def make_response_handler(
    llm: object,
    session_factory: object,
    executors: Mapping[ActionType, ActionExecutor],
    cfg: object,
    catalog: PlaybookCatalog,
    *,
    intel: object | None = None,
    memory: object | None = None,
    feedback_cfg: object | None = None,
) -> object:
    """Return a StageHandler closed over action tools, session factory, catalog, and config.

    This is the ONLY stage injected with action executors (Constitution III).
    Pass A: select → classify → execute auto + park destructive + verify (if applied).
    Pass B: execute the already-approved plan (no LLM) + verify.
    Optional intel and memory retrievers are injected for the verification re-check.
    """

    async def run_response(incident: Incident) -> StageResult:
        async with session_factory() as session:  # type: ignore[union-attr]
            approval_repo = ApprovalRepository(session)
            audit_repo = AuditRepository(session)

            approved = await approval_repo.get_approved_pending_for(incident.id)

            if approved is not None:
                return await _pass_b(
                    incident=incident,
                    approved=approved,
                    audit_repo=audit_repo,
                    executors=executors,
                    cfg=cfg,
                    intel=intel,
                    memory=memory,
                )

            return await _pass_a(
                incident=incident,
                catalog=catalog,
                llm=llm,
                cfg=cfg,
                audit_repo=audit_repo,
                approval_repo=approval_repo,
                executors=executors,
                intel=intel,
                memory=memory,
                feedback_cfg=feedback_cfg,
            )

    return run_response


# ---------------------------------------------------------------------------
# Shared verification tail — runs once, maps verdict → terminal StageResult
# ---------------------------------------------------------------------------


async def _finalize_with_verification(
    *,
    incident: Incident,
    results: list[ActionResult],
    applied_actions: list[RemediationAction],
    executors: Mapping[ActionType, ActionExecutor],
    intel: object | None,
    memory: object | None,
    cfg: object | None,
    audit_repo: object,
    base_evidence: dict,
    resolved_disposition: str,
    tokens_consumed: int,
    note: str,
) -> StageResult:
    """Run the verification tail (if enabled) and build the terminal StageResult.

    Shared by both passes. `base_evidence` is the pass-specific `response` sub-dict WITHOUT the
    `results`/`verification` keys (this helper fills those). Idempotent: skips re-verification when
    `evidence["response"]["verification"]` is already present. Fail-closed: any error → UNVERIFIED.
    """
    existing_evidence: dict = (incident.evidence or {}).get("response", {})
    verify_cfg_on: bool = getattr(cfg, "verify_remediation", True) if cfg else True
    applied = [r for r in results if r.status == ActionStatus.APPLIED]

    # Verification disabled / nothing applied / already verified → plain resolve.
    if not (verify_cfg_on and applied and "verification" not in existing_evidence):
        return StageResult(
            stage=StageName.RESPONSE,
            outcome=StageOutcome.RESOLVED,
            tokens_consumed=tokens_consumed,
            disposition=resolved_disposition,
            evidence_patch={
                "response": {
                    **base_evidence,
                    "results": [r.model_dump(mode="json") for r in results],
                }
            },
            note=note,
        )

    try:
        vr = await verify_remediation(
            applied_results=results,
            applied_actions=applied_actions,
            executors=executors,
            intel=intel,
            memory=memory,
            cfg=cfg or object(),
        )
    except Exception as exc:
        _logger.warning("verification_error", error=str(exc))
        vr = VerificationRecord(
            verdict=VerificationVerdict.UNVERIFIED,
            per_action=results,
            signals=[],
            rationale="verification_error: " + str(exc)[:200],
        )

    response_evidence = {
        **base_evidence,
        "results": [r.model_dump(mode="json") for r in vr.per_action],
        "verification": vr.model_dump(mode="json"),
    }
    verdict_note = f"{note} | verdict={vr.verdict.value}"

    if vr.verdict in (VerificationVerdict.UNVERIFIED, VerificationVerdict.REGRESSED):
        await _append_verification_audit(audit_repo, incident.id, vr.verdict)
        return StageResult(
            stage=StageName.RESPONSE,
            outcome=StageOutcome.UNVERIFIED,
            tokens_consumed=tokens_consumed,
            disposition=None,
            evidence_patch={"response": response_evidence},
            note=verdict_note,
        )

    return StageResult(
        stage=StageName.RESPONSE,
        outcome=StageOutcome.RESOLVED,
        tokens_consumed=tokens_consumed,
        disposition=resolved_disposition,
        evidence_patch={"response": response_evidence},
        note=verdict_note,
    )


# ---------------------------------------------------------------------------
# Pass A — forward path
# ---------------------------------------------------------------------------


async def _pass_a(
    *,
    incident: Incident,
    catalog: PlaybookCatalog,
    llm: object,
    cfg: object,
    audit_repo: object,
    approval_repo: object,
    executors: Mapping[ActionType, ActionExecutor],
    intel: object | None = None,
    memory: object | None = None,
    feedback_cfg: object | None = None,
) -> StageResult:
    """Pass A — forward path: select → classify → execute auto / park destructive → verify."""
    plan_raw, tokens_consumed = await select_playbook(
        incident, catalog, llm, cfg, feedback_cfg=feedback_cfg
    )
    plan = classify(plan_raw, cfg)

    auto_actions = [a for a in plan.actions if a.risk == RiskClass.AUTO]
    approval_actions = [a for a in plan.actions if a.risk == RiskClass.APPROVAL_REQUIRED]

    results: list[ActionResult] = []
    for action in auto_actions:
        result = await _execute_with_audit(
            action=action,
            incident_id=incident.id,
            actor="response_agent",
            audit_repo=audit_repo,
            executors=executors,
        )
        results.append(result)

    note = f"playbook={plan.playbook_id} selected_by={plan.selected_by}: {plan.rationale}"[:200]

    # Park destructive actions → await human approval.
    if approval_actions:
        timeout_s: int = getattr(cfg, "approval_timeout_s", 1800)
        deadline_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=timeout_s)

        approval_id: int = await approval_repo.create_pending(  # type: ignore[union-attr]
            incident_id=incident.id,
            plan_id=plan.plan_id,
            pending_actions=[a.model_dump(mode="json") for a in approval_actions],
            rationale=plan.rationale,
            deadline_at=deadline_at,
        )

        return StageResult(
            stage=StageName.RESPONSE,
            outcome=StageOutcome.NEEDS_APPROVAL,
            tokens_consumed=tokens_consumed,
            disposition=None,
            evidence_patch={
                "response": {
                    "plan": plan.model_dump(mode="json"),
                    "results": [r.model_dump(mode="json") for r in results],
                    "approval_id": approval_id,
                }
            },
            note=note,
        )

    # Auto-only plan → verification tail.
    return await _finalize_with_verification(
        incident=incident,
        results=results,
        applied_actions=auto_actions,
        executors=executors,
        intel=intel,
        memory=memory,
        cfg=cfg,
        audit_repo=audit_repo,
        base_evidence={"plan": plan.model_dump(mode="json"), "approval_id": None},
        resolved_disposition="auto_remediated",
        tokens_consumed=tokens_consumed,
        note=note,
    )


# ---------------------------------------------------------------------------
# Pass B — resume path (execute approved plan, no LLM)
# ---------------------------------------------------------------------------


async def _pass_b(
    *,
    incident: Incident,
    approved: object,
    audit_repo: object,
    executors: Mapping[ActionType, ActionExecutor],
    cfg: object | None = None,
    intel: object | None = None,
    memory: object | None = None,
) -> StageResult:
    """Pass B — resume: execute the approved plan (no LLM) → verify."""
    decided_by: str = getattr(approved, "decided_by", "admin") or "admin"
    pending_actions_raw: list = getattr(approved, "pending_actions", []) or []
    plan_id: str = getattr(approved, "plan_id", "") or ""

    approved_actions: list[RemediationAction] = []
    results: list[ActionResult] = []
    for action_dict in pending_actions_raw:
        try:
            action = RemediationAction.model_validate(action_dict)
        except Exception:
            continue
        approved_actions.append(action)
        result = await _execute_with_audit(
            action=action,
            incident_id=incident.id,
            actor=decided_by,
            audit_repo=audit_repo,
            executors=executors,
        )
        results.append(result)

    note = f"resume execution: approved by {decided_by}"

    return await _finalize_with_verification(
        incident=incident,
        results=results,
        applied_actions=approved_actions,
        executors=executors,
        intel=intel,
        memory=memory,
        cfg=cfg,
        audit_repo=audit_repo,
        base_evidence={
            "pass": "B",
            "plan_id": plan_id,
            "approval_id": getattr(approved, "id", None),
        },
        resolved_disposition="remediated",
        tokens_consumed=0,
        note=note,
    )


async def _append_verification_audit(
    audit_repo: object, incident_id: uuid.UUID, verdict: VerificationVerdict
) -> None:
    """Append one audit row for a verification outcome (best-effort, never raises)."""
    try:
        await audit_repo.append(  # type: ignore[union-attr]
            incident_id=incident_id,
            actor="verifier",
            action="verification",
            target=None,
            outcome=verdict.value,
            idempotency_key=None,
        )
    except Exception as exc:
        _logger.warning("verification_audit_error", error=str(exc))


# ---------------------------------------------------------------------------
# Legacy stub — fallback for degraded boot (supervisor_provider uses it when no LLM/DB)
# ---------------------------------------------------------------------------


async def run_response(incident: Incident) -> StageResult:
    """Fallback stub used when LLM or DB is absent at boot (degraded mode)."""
    flags: list[str] = (incident.evidence or {}).get("flags", [])
    if "destructive" in flags:
        return StageResult(
            stage=StageName.RESPONSE, outcome=StageOutcome.NEEDS_APPROVAL, tokens_consumed=0
        )
    return StageResult(
        stage=StageName.RESPONSE,
        outcome=StageOutcome.RESOLVED,
        disposition="auto_remediated",
        tokens_consumed=0,
    )
