"""e2e test — T024: grounded incident → terminal disposition via the supervisor.

Tests the full supervisor flow using the real stub handlers (no heavy ML models):
- Fast-path (noise → resolved, zero stages; critical → resolved via stub response)
- Ambiguous full depth (triage → enrichment → response → resolved)
- Destructive action → awaiting_approval (parked)
- Fault injection: stage ToolError → escalated, worker stays alive
- Cap breach → escalated, worker stays alive
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "incidents"


def _incident_from_file(path: Path):
    from backend.domain.incident import Incident, IncidentStatus, Severity

    data = json.loads(path.read_text())
    return Incident(
        id=uuid.UUID(data["id"]),
        status=IncidentStatus(data["status"]),
        severity=Severity(data["severity"]),
        correlation_id=data["correlation_id"],
        dedup_fingerprint=data["dedup_fingerprint"],
        source=data["source"],
        raw_alert=data.get("raw_alert", {}),
        evidence=data.get("evidence"),
    )


class FakeRepo:
    def __init__(self, incident) -> None:
        self._incident = incident.model_copy(deep=True)
        self.advances: list[dict] = []

    async def get(self, incident_id: uuid.UUID):
        if self._incident.id == incident_id:
            return self._incident
        return None

    async def advance_status(self, incident_id, *, expected, target, disposition=None) -> bool:
        if self._incident.id != incident_id or self._incident.status != expected:
            return False
        self.advances.append({"from": expected, "to": target, "disposition": disposition})
        self._incident = self._incident.model_copy(update={"status": target, "disposition": disposition})
        return True


def _make_supervisor(stages=None):
    from backend.agents.enrichment import run_enrichment
    from backend.agents.response import run_response
    from backend.agents.triage import run_triage
    from backend.domain.pipeline import StageName
    from backend.infra.config import SupervisorSettings
    from backend.infra.tracing import build_tracer
    from backend.services.supervisor import Supervisor

    return Supervisor(
        stages=stages or {
            StageName.TRIAGE: run_triage,
            StageName.ENRICHMENT: run_enrichment,
            StageName.RESPONSE: run_response,
        },
        cfg=SupervisorSettings(),
        tracer=build_tracer(exporter=None),
    )


@pytest.mark.e2e
class TestPipelineDispositions:
    async def test_noise_fast_path(self) -> None:
        """Low-severity incident → resolved with zero stage calls (SC-003)."""
        from backend.domain.incident import IncidentStatus
        from backend.domain.pipeline import StageName, StageOutcome, StageResult

        calls: list[str] = []

        async def triage(inc):
            calls.append("triage")
            return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.RESOLVED)

        incident = _incident_from_file(FIXTURE_DIR / "noise_low.json")
        repo = FakeRepo(incident)
        sv = _make_supervisor({StageName.TRIAGE: triage})
        await sv.run_incident(incident.id, repo)

        final = await repo.get(incident.id)
        assert final.status == IncidentStatus.RESOLVED
        assert final.disposition == "auto_resolved_noise"
        assert calls == []

    async def test_ambiguous_full_depth_with_real_stubs(self) -> None:
        """Ambiguous high-severity incident walks full triage→enrichment→response→resolved."""
        from backend.domain.incident import IncidentStatus

        incident = _incident_from_file(FIXTURE_DIR / "ambiguous_full_depth.json")
        repo = FakeRepo(incident)
        sv = _make_supervisor()
        await sv.run_incident(incident.id, repo)

        final = await repo.get(incident.id)
        assert final.status == IncidentStatus.RESOLVED

        targets = [a["to"] for a in repo.advances]
        assert IncidentStatus.TRIAGING in targets
        assert IncidentStatus.ENRICHING in targets
        assert IncidentStatus.RESPONDING in targets
        assert IncidentStatus.RESOLVED in targets

    async def test_destructive_parks(self) -> None:
        """Critical destructive incident parks in awaiting_approval."""
        from backend.domain.incident import IncidentStatus

        incident = _incident_from_file(FIXTURE_DIR / "destructive_parks.json")
        repo = FakeRepo(incident)
        sv = _make_supervisor()
        await sv.run_incident(incident.id, repo)

        final = await repo.get(incident.id)
        assert final.status == IncidentStatus.AWAITING_APPROVAL
        assert final.disposition == "awaiting_approval_destructive"

    async def test_injected_stage_error_escalates(self) -> None:
        """Injected non-retryable ToolError → escalated, worker survives (SC-004)."""
        from backend.domain.incident import IncidentStatus
        from backend.domain.pipeline import StageName, ToolError

        async def error_triage(inc):
            raise ToolError(retryable=False, kind="inject_error")

        incident = _incident_from_file(FIXTURE_DIR / "stage_error_escalates.json")
        repo = FakeRepo(incident)
        sv = _make_supervisor({StageName.TRIAGE: error_triage})

        # Must not raise — worker survives
        await sv.run_incident(incident.id, repo)

        final = await repo.get(incident.id)
        assert final.status == IncidentStatus.ESCALATED
        assert final.disposition == "escalated_stage_error"

    async def test_cap_breach_escalates(self) -> None:
        """Stages looping ADVANCE past max_steps → escalated_step_cap, worker survives (SC-002)."""
        from backend.domain.incident import IncidentStatus
        from backend.domain.pipeline import StageName, StageOutcome, StageResult
        from backend.infra.config import SupervisorSettings
        from backend.infra.tracing import build_tracer
        from backend.services.supervisor import Supervisor

        async def always_advance_triage(inc):
            return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.ADVANCE)

        async def always_advance_enrichment(inc):
            return StageResult(stage=StageName.ENRICHMENT, outcome=StageOutcome.ADVANCE)

        async def always_advance_response(inc):
            return StageResult(stage=StageName.RESPONSE, outcome=StageOutcome.ESCALATE)

        cfg = SupervisorSettings(max_steps=2)
        sv = Supervisor(
            stages={
                StageName.TRIAGE: always_advance_triage,
                StageName.ENRICHMENT: always_advance_enrichment,
                StageName.RESPONSE: always_advance_response,
            },
            cfg=cfg,
            tracer=build_tracer(exporter=None),
        )

        incident = _incident_from_file(FIXTURE_DIR / "cap_breach_escalates.json")
        repo = FakeRepo(incident)
        await sv.run_incident(incident.id, repo)

        final = await repo.get(incident.id)
        assert final.status == IncidentStatus.ESCALATED
        assert final.disposition == "escalated_step_cap"

    async def test_idempotent_re_delivery(self) -> None:
        """Re-delivering a terminal incident is a no-op — no duplicate processing (SC-005)."""
        from backend.domain.incident import IncidentStatus, Severity
        from backend.domain.pipeline import StageName, StageOutcome, StageResult

        async def triage(inc):
            return StageResult(stage=StageName.TRIAGE, outcome=StageOutcome.RESOLVED)

        incident = _incident_from_file(FIXTURE_DIR / "ambiguous_resolved_at_triage.json")
        repo = FakeRepo(incident)
        sv = _make_supervisor({StageName.TRIAGE: triage})

        await sv.run_incident(incident.id, repo)
        advances_after_first = len(repo.advances)

        # Re-deliver
        await sv.run_incident(incident.id, repo)
        assert len(repo.advances) == advances_after_first  # no new transitions
