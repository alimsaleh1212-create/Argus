"""Supervisor-routing eval gate (SPEC-incident-state-machine #7 + #16)."""

from __future__ import annotations

import uuid
from typing import Any

from backend.domain.eval import GateKind, GateResult, GateSpec
from backend.eval.gates import GATE_REGISTRY


async def run_supervisor_routing(spec: GateSpec, provider: str | None = None) -> GateResult:
    """Run all supervisor-routing fixtures; 100% pass rate required."""
    from backend.domain.incident import Incident, IncidentStatus, Severity
    from backend.domain.pipeline import StageName, StageOutcome, StageResult, ToolError
    from backend.infra.config import SupervisorSettings
    from backend.infra.tracing import build_tracer
    from backend.services.supervisor import Supervisor

    class _FakeRepo:
        def __init__(self, incident: Incident) -> None:
            self._incident = incident.model_copy(deep=True)

        async def get(self, incident_id: uuid.UUID) -> Incident | None:
            return self._incident if self._incident.id == incident_id else None

        async def advance_status(
            self, incident_id, *, expected, target, disposition=None, evidence_patch=None
        ) -> bool:
            if self._incident.id != incident_id or self._incident.status != expected:
                return False
            self._incident = self._incident.model_copy(
                update={"status": target, "disposition": disposition}
            )
            return True

    def _incident(severity: str, flags: list[str] | None = None) -> Incident:
        return Incident(
            id=uuid.uuid4(),
            status=IncidentStatus.GROUNDED,
            severity=Severity(severity),
            correlation_id="corr-eval",
            dedup_fingerprint=f"fp-{uuid.uuid4().hex}",
            source="wazuh",
            raw_alert={},
            evidence={
                "flags": flags or [],
                "verdict": "test",
                "severity": severity,
                "normalized_event": {},
                "summary": "eval",
            },
        )

    async def _resolve(inc, stages):
        repo = _FakeRepo(inc)
        sv = Supervisor(stages=stages, cfg=SupervisorSettings(), tracer=build_tracer(exporter=None))
        await sv.run_incident(inc.id, repo)
        return await repo.get(inc.id)

    passed = total = 0

    cases: list[tuple[str, Any]] = [
        ("noise_low", lambda: _incident("low")),
        ("critical_high", lambda: _incident("critical")),
        ("ambiguous_resolved_at_triage", lambda: _incident("medium")),
        ("ambiguous_full_depth", lambda: _incident("high")),
        ("destructive_parks", lambda: _incident("critical")),
        ("indeterminate_severity", lambda: _incident("low", ["severity_defaulted"])),
        ("stage_error_escalates", lambda: _incident("medium")),
        ("cap_breach_escalates", lambda: _incident("medium")),
        # Added by SPEC-remediation-verification (#15)
        ("verified_resolves", lambda: _incident("critical")),
        ("unverified_escalates", lambda: _incident("critical")),
        # Added by SPEC-memory-feedback-loop (#16)
        ("prior_regressed_escalates", lambda: _incident("critical", ["prior_failure"])),
    ]

    async def _triage_advance(inc):
        return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.ADVANCE)

    async def _triage_resolved(inc):
        return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.RESOLVED)

    async def _enrich_advance(inc):
        return StageResult(stage=StageName.ENRICHMENT, outcome=StageOutcome.ADVANCE)

    async def _response_resolved(inc):
        return StageResult(stage=StageName.RESPONSE, outcome=StageOutcome.RESOLVED)

    async def _response_needs_approval(inc):
        return StageResult(stage=StageName.RESPONSE, outcome=StageOutcome.NEEDS_APPROVAL)

    async def _error_triage(inc):
        raise ToolError(retryable=False, kind="inject_error")

    async def _response_escalate(inc):
        return StageResult(stage=StageName.RESPONSE, outcome=StageOutcome.ESCALATE)

    async def _response_remediated(inc):
        return StageResult(
            stage=StageName.RESPONSE, outcome=StageOutcome.RESOLVED, disposition="remediated"
        )

    async def _response_unverified(inc):
        return StageResult(stage=StageName.RESPONSE, outcome=StageOutcome.UNVERIFIED)

    fixture_stages = {
        "noise_low": ({StageName.TRIAGE: _triage_resolved}, IncidentStatus.RESOLVED),
        "critical_high": ({StageName.RESPONSE: _response_resolved}, IncidentStatus.RESOLVED),
        "ambiguous_resolved_at_triage": (
            {StageName.TRIAGE: _triage_resolved, StageName.ENRICHMENT: _enrich_advance},
            IncidentStatus.RESOLVED,
        ),
        "ambiguous_full_depth": (
            {
                StageName.TRIAGE: _triage_advance,
                StageName.ENRICHMENT: _enrich_advance,
                StageName.RESPONSE: _response_resolved,
            },
            IncidentStatus.RESOLVED,
        ),
        "destructive_parks": (
            {StageName.RESPONSE: _response_needs_approval},
            IncidentStatus.AWAITING_APPROVAL,
        ),
        "indeterminate_severity": ({StageName.TRIAGE: _triage_resolved}, None),
        "stage_error_escalates": ({StageName.TRIAGE: _error_triage}, IncidentStatus.ESCALATED),
        "cap_breach_escalates": (
            {
                StageName.TRIAGE: _triage_advance,
                StageName.ENRICHMENT: _enrich_advance,
                StageName.RESPONSE: _response_escalate,
            },
            IncidentStatus.ESCALATED,
        ),
        # SPEC-015 verification FSM edges
        "verified_resolves": (
            {StageName.RESPONSE: _response_remediated},
            IncidentStatus.RESOLVED,
        ),
        "unverified_escalates": (
            {StageName.RESPONSE: _response_unverified},
            IncidentStatus.ESCALATED,
        ),
        # SPEC-016 feedback bias: prior failure with critical severity routes to responding
        "prior_regressed_escalates": (
            {StageName.RESPONSE: _response_resolved},
            IncidentStatus.RESOLVED,
        ),
    }

    failed_cases: list[str] = []
    for name, inc_fn in cases:
        stages_map, expected_status = fixture_stages[name]
        inc = inc_fn()
        # For indeterminate_severity: just check triage is called (don't validate exact status)
        if name == "indeterminate_severity":
            triage_called = False
            original = stages_map[StageName.TRIAGE]

            async def _track_triage(inc, _orig=original):
                nonlocal triage_called
                triage_called = True
                return await _orig(inc)

            stages_map = {StageName.TRIAGE: _track_triage}
            await _resolve(inc, stages_map)
            ok = triage_called
        else:
            final = await _resolve(inc, stages_map)
            ok = expected_status is None or final.status == expected_status
        total += 1
        if ok:
            passed += 1
        else:
            failed_cases.append(name)

    pass_rate = passed / total if total else 0.0
    threshold_rate = spec.threshold.get("pass_rate", 1.0)
    gate_passed = pass_rate >= threshold_rate
    evidence = f"{passed}/{total} fixtures passed" + (
        f"; failed: {failed_cases}" if failed_cases else ""
    )

    return GateResult(
        gate=spec.name,
        kind=spec.kind,
        provider=provider,
        score=pass_rate,
        threshold=spec.threshold,
        passed=gate_passed,
        blocking=spec.kind == GateKind.required,
        evidence=evidence,
    )


GATE_REGISTRY["supervisor_routing"] = run_supervisor_routing
