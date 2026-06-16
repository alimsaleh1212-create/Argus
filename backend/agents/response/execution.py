"""Per-action execution with pre-execution idempotency check + audit-row write."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from backend.domain.pipeline import ToolError
from backend.domain.response import (
    ActionExecutor,
    ActionResult,
    ActionStatus,
    ActionType,
    RemediationAction,
)


async def _execute_with_audit(
    *,
    action: RemediationAction,
    incident_id: uuid.UUID,
    actor: str,
    audit_repo: object,
    executors: Mapping[ActionType, ActionExecutor],
) -> ActionResult:
    """Execute one action with pre-execution idempotency check and audit row write."""
    # Pre-execution idempotency check (T032 — check before executing, never double-execute)
    already = await audit_repo.is_applied(action.idempotency_key)  # type: ignore[union-attr]
    if already:
        return ActionResult(
            type=action.type,
            target=action.target,
            status=ActionStatus.APPLIED,
            detail="idempotent_skip",
        )

    executor = executors.get(action.type)
    if executor is None:
        raise ToolError(retryable=False, kind="no_executor_for_action")

    try:
        result = await executor.execute(action)
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(retryable=True, kind="executor_transient") from exc

    idem_key = action.idempotency_key if result.status == ActionStatus.APPLIED else None
    await audit_repo.append(  # type: ignore[union-attr]
        incident_id=incident_id,
        actor=actor,
        action=action.type.value,
        target=action.target,
        outcome=result.status.value,
        idempotency_key=idem_key,
    )
    return result
