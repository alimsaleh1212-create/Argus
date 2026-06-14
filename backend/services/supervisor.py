"""Deterministic incident supervisor — plain async state machine.

No LLM, no LangGraph. Single writer of incident status/disposition (SD4 / Constitution III).
Stages are pure handlers that return a StageResult; the supervisor persists all transitions.
"""

from __future__ import annotations

import uuid

from backend.domain.incident import Incident, IncidentStatus
from backend.domain.pipeline import StageHandler, StageName, StageOutcome, StageResult, ToolError
from backend.domain.telemetry import SpanKind
from backend.infra.config import SupervisorSettings
from backend.infra.logging import bind_incident, get_logger
from backend.infra.tracing import _Tracer, span

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Routing sentinels (grounded fast-path decision — not a StageOutcome)
# ---------------------------------------------------------------------------

_ROUTE_NOISE = "route:noise"
_ROUTE_CRITICAL = "route:critical"
_ROUTE_AMBIGUOUS = "route:ambiguous"

# ---------------------------------------------------------------------------
# Disposition vocabulary
# ---------------------------------------------------------------------------

DISP_AUTO_RESOLVED_NOISE = "auto_resolved_noise"
DISP_AUTO_RESOLVED_TRIAGE = "auto_resolved_triage"
DISP_AUTO_RESOLVED_ENRICHMENT = "auto_resolved_enrichment"
DISP_AUTO_REMEDIATED = "auto_remediated"
DISP_REMEDIATED = "remediated"
DISP_REJECTED_BY_HUMAN = "rejected_by_human"
DISP_APPROVAL_EXPIRED = "approval_expired"
DISP_REMEDIATION_UNVERIFIED = "remediation_unverified"  # RESERVED §v2c — unused in v1
DISP_ESCALATED_TRIAGE = "escalated_triage"
DISP_ESCALATED_ENRICHMENT = "escalated_enrichment"
DISP_ESCALATED_RESPONSE = "escalated_response"
DISP_ESCALATED_STEP_CAP = "escalated_step_cap"
DISP_ESCALATED_TOKEN_CAP = "escalated_token_cap"
DISP_ESCALATED_STAGE_ERROR = "escalated_stage_error"
DISP_ESCALATED_ILLEGAL = "escalated_illegal_transition"
DISP_AWAITING_APPROVAL = "awaiting_approval_destructive"

# ---------------------------------------------------------------------------
# Transition table  (state, outcome_or_route) → (next_state, disposition | None)
# ---------------------------------------------------------------------------

TRANSITIONS: dict[tuple[IncidentStatus, str], tuple[IncidentStatus, str | None]] = {
    # Fast-path routing (grounded)
    (IncidentStatus.GROUNDED, _ROUTE_NOISE): (IncidentStatus.RESOLVED, DISP_AUTO_RESOLVED_NOISE),
    (IncidentStatus.GROUNDED, _ROUTE_CRITICAL): (IncidentStatus.RESPONDING, None),
    (IncidentStatus.GROUNDED, _ROUTE_AMBIGUOUS): (IncidentStatus.TRIAGING, None),
    # Triage outcomes
    (IncidentStatus.TRIAGING, StageOutcome.RESOLVED): (
        IncidentStatus.RESOLVED,
        DISP_AUTO_RESOLVED_TRIAGE,
    ),
    (IncidentStatus.TRIAGING, StageOutcome.ADVANCE): (IncidentStatus.ENRICHING, None),
    (IncidentStatus.TRIAGING, StageOutcome.ESCALATE): (
        IncidentStatus.ESCALATED,
        DISP_ESCALATED_TRIAGE,
    ),
    # Enrichment outcomes
    (IncidentStatus.ENRICHING, StageOutcome.ADVANCE): (IncidentStatus.RESPONDING, None),
    (IncidentStatus.ENRICHING, StageOutcome.RESOLVED): (
        IncidentStatus.RESOLVED,
        DISP_AUTO_RESOLVED_ENRICHMENT,
    ),
    (IncidentStatus.ENRICHING, StageOutcome.ESCALATE): (
        IncidentStatus.ESCALATED,
        DISP_ESCALATED_ENRICHMENT,
    ),
    # Response outcomes — disposition=None so handler-proposed value passes through (RD8)
    (IncidentStatus.RESPONDING, StageOutcome.RESOLVED): (IncidentStatus.RESOLVED, None),
    (IncidentStatus.RESPONDING, StageOutcome.NEEDS_APPROVAL): (
        IncidentStatus.AWAITING_APPROVAL,
        DISP_AWAITING_APPROVAL,
    ),
    (IncidentStatus.RESPONDING, StageOutcome.ESCALATE): (
        IncidentStatus.ESCALATED,
        DISP_ESCALATED_RESPONSE,
    ),
}

# ---------------------------------------------------------------------------
# State classes
# ---------------------------------------------------------------------------

_ENTRY_STATES = frozenset({IncidentStatus.GROUNDED})
_IN_FLIGHT_STATES = frozenset(
    {IncidentStatus.TRIAGING, IncidentStatus.ENRICHING, IncidentStatus.RESPONDING}
)
_TERMINAL_STATES = frozenset(
    {IncidentStatus.RESOLVED, IncidentStatus.ESCALATED, IncidentStatus.FAILED}
)
_PARKED_STATES = frozenset({IncidentStatus.AWAITING_APPROVAL})

_STATUS_TO_STAGE: dict[IncidentStatus, StageName] = {
    IncidentStatus.TRIAGING: StageName.TRIAGE,
    IncidentStatus.ENRICHING: StageName.ENRICHMENT,
    IncidentStatus.RESPONDING: StageName.RESPONSE,
}

# ---------------------------------------------------------------------------
# Routing (pure, config-backed — SD5)
# ---------------------------------------------------------------------------


def route_grounded(incident: Incident, cfg: SupervisorSettings) -> str:
    """Config-backed routing decision for a grounded incident. Returns a _ROUTE_* sentinel."""
    sev = incident.severity.value
    flags: list[str] = (incident.evidence or {}).get("flags", [])
    if "severity_defaulted" in flags:
        return _ROUTE_AMBIGUOUS
    if sev in cfg.fast_path_autoclose_severities:
        return _ROUTE_NOISE
    if sev in cfg.fast_path_critical_severities:
        return _ROUTE_CRITICAL
    return _ROUTE_AMBIGUOUS


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------


class Supervisor:
    """Deterministic incident state machine. Single writer of status/disposition.

    Injected with a stage-handler registry (substitutable in tests), typed settings,
    and the #2 observability tracer. Makes no LLM call (SC-006).
    """

    def __init__(
        self,
        stages: dict[StageName, StageHandler],
        cfg: SupervisorSettings,
        tracer: _Tracer,
    ) -> None:
        self._stages = stages
        self._cfg = cfg
        self._tracer = tracer

    async def run_incident(self, incident_id: uuid.UUID, repo: object) -> None:
        """Drive a grounded (or in-flight) incident to a terminal disposition.

        Idempotent: terminal / parked incidents are no-ops.
        In-flight incidents resume from their persisted stage.
        """
        bind_incident(str(incident_id))
        with span(
            self._tracer,
            "supervisor.run",
            SpanKind.ROOT,
            str(incident_id),
            attrs={"incident_id": str(incident_id)},
        ):
            incident = await repo.get(incident_id)
            if incident is None:
                logger.warning("supervisor_incident_not_found", incident_id=str(incident_id))
                return

            current_status = incident.status

            # No-op for terminal or parked states
            if current_status in _TERMINAL_STATES or current_status in _PARKED_STATES:
                logger.info(
                    "supervisor_noop", status=current_status.value, incident_id=str(incident_id)
                )
                return

            # Reject invalid entry states (received/grounding — not supervisor territory)
            if current_status not in _ENTRY_STATES and current_status not in _IN_FLIGHT_STATES:
                logger.warning("supervisor_invalid_entry_state", status=current_status.value)
                return

            # --- Grounded: route to first state ---
            if current_status == IncidentStatus.GROUNDED:
                route_key = route_grounded(incident, self._cfg)
                next_status, disp = TRANSITIONS[(IncidentStatus.GROUNDED, route_key)]
                advanced = await repo.advance_status(
                    incident_id,
                    expected=IncidentStatus.GROUNDED,
                    target=next_status,
                    disposition=disp,
                )
                if not advanced:
                    logger.info("supervisor_guard_lost_routing", incident_id=str(incident_id))
                    return
                logger.info(
                    "supervisor_routed",
                    route=route_key,
                    to_status=next_status.value,
                    incident_id=str(incident_id),
                )
                if next_status in _TERMINAL_STATES:
                    return
                current_status = next_status

            # --- In-flight stage loop ---
            steps = 0
            tokens = 0

            while current_status in _IN_FLIGHT_STATES:
                # Cap checks before invoking the stage
                if steps >= self._cfg.max_steps:
                    await repo.advance_status(
                        incident_id,
                        expected=current_status,
                        target=IncidentStatus.ESCALATED,
                        disposition=DISP_ESCALATED_STEP_CAP,
                    )
                    logger.warning("supervisor_step_cap", steps=steps, incident_id=str(incident_id))
                    return

                if tokens >= self._cfg.max_tokens:
                    await repo.advance_status(
                        incident_id,
                        expected=current_status,
                        target=IncidentStatus.ESCALATED,
                        disposition=DISP_ESCALATED_TOKEN_CAP,
                    )
                    logger.warning(
                        "supervisor_token_cap", tokens=tokens, incident_id=str(incident_id)
                    )
                    return

                stage_name = _STATUS_TO_STAGE[current_status]
                handler = self._stages[stage_name]
                result: StageResult | None = None

                for attempt in range(self._cfg.max_stage_retries + 1):
                    try:
                        with span(
                            self._tracer,
                            f"supervisor.stage.{stage_name.value}",
                            SpanKind.AGENT_STEP,
                            str(incident_id),
                            attrs={"stage": stage_name.value, "attempt": attempt},
                        ):
                            result = await handler(incident)
                        break
                    except ToolError as exc:
                        if not exc.retryable or attempt >= self._cfg.max_stage_retries:
                            await repo.advance_status(
                                incident_id,
                                expected=current_status,
                                target=IncidentStatus.ESCALATED,
                                disposition=DISP_ESCALATED_STAGE_ERROR,
                            )
                            logger.warning(
                                "supervisor_stage_error",
                                stage=stage_name.value,
                                kind=exc.kind,
                                retryable=exc.retryable,
                                incident_id=str(incident_id),
                            )
                            return
                        logger.info(
                            "supervisor_stage_retry",
                            stage=stage_name.value,
                            attempt=attempt,
                            incident_id=str(incident_id),
                        )
                    except Exception as exc:
                        await repo.advance_status(
                            incident_id,
                            expected=current_status,
                            target=IncidentStatus.ESCALATED,
                            disposition=DISP_ESCALATED_STAGE_ERROR,
                        )
                        logger.error(
                            "supervisor_unexpected_error",
                            stage=stage_name.value,
                            error=type(exc).__name__,
                            incident_id=str(incident_id),
                        )
                        return

                if result is None:
                    await repo.advance_status(
                        incident_id,
                        expected=current_status,
                        target=IncidentStatus.ESCALATED,
                        disposition=DISP_ESCALATED_STAGE_ERROR,
                    )
                    return

                steps += 1
                tokens += result.tokens_consumed

                # Resolve transition from table
                edge = TRANSITIONS.get((current_status, result.outcome))
                if edge is None:
                    await repo.advance_status(
                        incident_id,
                        expected=current_status,
                        target=IncidentStatus.ESCALATED,
                        disposition=DISP_ESCALATED_ILLEGAL,
                    )
                    logger.warning(
                        "supervisor_illegal_transition",
                        from_status=current_status.value,
                        outcome=str(result.outcome),
                        incident_id=str(incident_id),
                    )
                    return

                next_status, table_disp = edge
                # Stage-proposed disposition used only when the table has none
                final_disp = table_disp or result.disposition

                advanced = await repo.advance_status(
                    incident_id,
                    expected=current_status,
                    target=next_status,
                    disposition=final_disp,
                    evidence_patch=result.evidence_patch,
                )
                if not advanced:
                    logger.info("supervisor_guard_lost_stage", incident_id=str(incident_id))
                    return

                logger.info(
                    "supervisor_transition",
                    from_status=current_status.value,
                    to_status=next_status.value,
                    stage=stage_name.value,
                    outcome=str(result.outcome),
                    incident_id=str(incident_id),
                )
                current_status = next_status

    async def resume_incident(
        self,
        incident_id: uuid.UUID,
        decision: str,
        repo: object,
        audit_repo: object | None = None,
        actor: str = "admin",
    ) -> str | None:
        """Apply the human approve/reject decision to a parked incident.

        approve → AWAITING_APPROVAL → RESPONDING, then re-drives run_incident to execute.
        reject  → AWAITING_APPROVAL → RESOLVED (rejected_by_human) + audit row.
        Returns the final disposition string (for the API response).
        """
        if decision == "approve":
            advanced = await repo.advance_status(
                incident_id,
                expected=IncidentStatus.AWAITING_APPROVAL,
                target=IncidentStatus.RESPONDING,
            )
            if not advanced:
                logger.info(
                    "supervisor_resume_guard_lost",
                    decision=decision,
                    incident_id=str(incident_id),
                )
                incident = await repo.get(incident_id)
                return getattr(incident, "disposition", None) if incident else None

            logger.info(
                "supervisor_resume_approved",
                incident_id=str(incident_id),
            )
            # Re-drive to execute the approved plan through the response stage (RD3)
            await self.run_incident(incident_id, repo)
            incident = await repo.get(incident_id)
            return getattr(incident, "disposition", None) if incident else None

        elif decision == "reject":
            advanced = await repo.advance_status(
                incident_id,
                expected=IncidentStatus.AWAITING_APPROVAL,
                target=IncidentStatus.RESOLVED,
                disposition=DISP_REJECTED_BY_HUMAN,
            )
            if not advanced:
                logger.info(
                    "supervisor_resume_guard_lost",
                    decision=decision,
                    incident_id=str(incident_id),
                )
                incident = await repo.get(incident_id)
                return getattr(incident, "disposition", None) if incident else None

            logger.info(
                "supervisor_resume_rejected",
                incident_id=str(incident_id),
            )
            # Write audit row for the rejection
            if audit_repo is not None:
                try:
                    await audit_repo.append(
                        incident_id=incident_id,
                        actor=actor,
                        action="approval_rejected",
                        target=None,
                        outcome="not_executed",
                    )
                except Exception:
                    pass
            return DISP_REJECTED_BY_HUMAN

        else:
            logger.warning(
                "supervisor_unknown_resume_decision",
                decision=decision,
                incident_id=str(incident_id),
            )
            return None

    async def expire_incident(
        self,
        incident_id: uuid.UUID,
        repo: object,
        audit_repo: object | None = None,
    ) -> bool:
        """Expire a parked incident whose approval deadline has elapsed (RD7).

        AWAITING_APPROVAL → ESCALATED (approval_expired). Nothing executes.
        Returns True iff the transition succeeded.
        """
        advanced = await repo.advance_status(
            incident_id,
            expected=IncidentStatus.AWAITING_APPROVAL,
            target=IncidentStatus.ESCALATED,
            disposition=DISP_APPROVAL_EXPIRED,
        )
        if not advanced:
            logger.info("supervisor_expire_guard_lost", incident_id=str(incident_id))
            return False

        logger.info("supervisor_incident_expired", incident_id=str(incident_id))
        if audit_repo is not None:
            try:
                await audit_repo.append(
                    incident_id=incident_id,
                    actor="timeout",
                    action="approval_expired",
                    target=None,
                    outcome="not_executed",
                )
            except Exception:
                pass
        return True
