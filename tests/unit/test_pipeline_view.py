"""Unit tests for the SOC pipeline-map data spine (M-a)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from backend.domain.dashboard import (
    BranchOutflow,
    PipelineSnapshot,
    StageNode,
    TerminalCounts,
)
from backend.domain.incident import IncidentStatus
from backend.services import pipeline_view, supervisor
from backend.services.pipeline_view import (
    STAGES,
    build_pipeline_snapshot,
    stage_branches,
    stage_in_flight,
    terminal_counts,
)


class TestPipelineDtos:
    def test_pipeline_snapshot_shape(self) -> None:
        snap = PipelineSnapshot(
            stages=[
                StageNode(
                    key="triage",
                    label="Triage",
                    in_flight=3,
                    branches=[BranchOutflow(to="resolved", count=2)],
                )
            ],
            terminals=TerminalCounts(resolved=10, escalated=4, awaiting=1),
            window_hours=24,
            generated_at=datetime.now(UTC),
        )
        assert snap.stages[0].in_flight == 3
        assert snap.stages[0].branches[0].to == "resolved"
        assert snap.stages[0].branches[0].count == 2
        assert snap.terminals.awaiting == 1
        assert snap.window_hours == 24

    def test_stage_node_branches_default_empty(self) -> None:
        node = StageNode(key="intake", label="Intake", in_flight=0)
        assert node.branches == []


class TestStageMapping:
    def test_stages_are_ordered(self) -> None:
        assert [k for k, _ in STAGES] == ["intake", "triage", "enrichment", "response"]

    def test_stage_in_flight_groups_statuses(self) -> None:
        counts = {
            "received": 2,
            "grounding": 1,
            "triaging": 4,
            "responding": 2,
            "awaiting_approval": 1,
            "resolved": 99,  # terminal — must NOT count toward any stage
        }
        inflight = stage_in_flight(counts)
        assert inflight == {"intake": 3, "triage": 4, "enrichment": 0, "response": 3}

    def test_stage_branches_maps_dispositions_and_ignores_unknown(self) -> None:
        disp = {
            "auto_resolved_triage": 2,
            "escalated_triage": 1,
            "auto_remediated": 5,
            "approval_expired": 1,
            "some_unknown_disposition": 9,
        }
        branches = stage_branches(disp)
        assert {b.to: b.count for b in branches["triage"]} == {"resolved": 2, "escalated": 1}
        assert {b.to: b.count for b in branches["response"]} == {"resolved": 5, "escalated": 1}
        # unknown disposition is attributed to no stage
        total_attributed = sum(b.count for stage in branches.values() for b in stage)
        assert total_attributed == 9  # unknown disposition (count 9) excluded; 2+1+5+1

    def test_terminal_counts_sums_by_branch_plus_awaiting(self) -> None:
        status = {"awaiting_approval": 2}
        disp = {
            "auto_resolved_triage": 3,
            "auto_remediated": 4,
            "escalated_enrichment": 1,
            "approval_expired": 2,
        }
        tc = terminal_counts(status, disp)
        assert tc.resolved == 7  # 3 + 4
        assert tc.escalated == 3  # 1 + 2
        assert tc.awaiting == 2


class TestBuildPipelineSnapshot:
    @pytest.mark.asyncio
    async def test_composes_from_repo_reads(self) -> None:
        repo = AsyncMock()
        repo.status_counts = AsyncMock(
            return_value={"triaging": 4, "responding": 1, "awaiting_approval": 1}
        )
        repo.disposition_counts_since = AsyncMock(
            return_value={"auto_resolved_triage": 2, "escalated_response": 1}
        )
        repo.list_in_flight_with_evidence = AsyncMock(return_value=[])

        snap = await build_pipeline_snapshot(repo, window_hours=24)

        assert isinstance(snap, PipelineSnapshot)
        assert [s.key for s in snap.stages] == ["intake", "triage", "enrichment", "response"]
        triage = next(s for s in snap.stages if s.key == "triage")
        assert triage.in_flight == 4
        assert {b.to: b.count for b in triage.branches} == {"resolved": 2}
        assert triage.incidents == []
        assert snap.terminals.escalated == 1
        assert snap.terminals.awaiting == 1
        assert snap.window_hours == 24
        repo.status_counts.assert_called_once()
        repo.disposition_counts_since.assert_called_once_with(window_hours=24)
        repo.list_in_flight_with_evidence.assert_called_once()


class TestStageIncidents:
    def _incident(self, **overrides):
        import uuid as _uuid

        from backend.domain.incident import Incident, IncidentStatus, Severity

        base: dict = {
            "id": _uuid.uuid4(),
            "status": IncidentStatus.TRIAGING,
            "severity": Severity.MEDIUM,
            "correlation_id": "c1",
            "dedup_fingerprint": "fp1",
            "source": "wazuh",
            "raw_alert": {},
            "evidence": {
                "summary": "SSH brute force",
                "triage": {"verdict": "real", "confidence": 0.82},
                "enrichment": {"assessment": "confirmed", "confidence": 0.71},
            },
            "updated_at": datetime.now(UTC),
        }
        base.update(overrides)
        return Incident(**base)

    def test_projects_triage_and_enrichment_scores(self) -> None:
        from backend.services.pipeline_view import _to_stage_incident

        view = _to_stage_incident(self._incident())
        assert view.triage_verdict == "real"
        assert view.triage_confidence == 0.82
        assert view.enrichment_assessment == "confirmed"
        assert view.enrichment_confidence == 0.71
        assert view.response_plan_id is None

    def test_projects_response_plan_and_verification(self) -> None:
        from backend.services.pipeline_view import _to_stage_incident

        inc = self._incident(
            evidence={
                "summary": "lateral movement",
                "response": {
                    "plan": {"playbook_id": "isolate_host", "selected_by": "deterministic"},
                    "verification": {"verdict": "verified"},
                },
            }
        )
        view = _to_stage_incident(inc)
        assert view.response_plan_id == "isolate_host"
        assert view.response_selected_by == "deterministic"
        assert view.response_verdict == "verified"

    def test_confidence_out_of_range_coerced_to_none(self) -> None:
        from backend.services.pipeline_view import _to_stage_incident

        inc = self._incident(
            evidence={"summary": "x", "triage": {"verdict": "real", "confidence": 1.4}}
        )
        assert _to_stage_incident(inc).triage_confidence is None

    def test_stage_incidents_groups_by_stage_and_drops_unmapped(self) -> None:
        from backend.domain.incident import IncidentStatus
        from backend.services.pipeline_view import stage_incidents

        triaging = self._incident()
        enriching = self._incident(status=IncidentStatus.ENRICHING)
        # 'grounded' maps to intake; a terminal status would be dropped, but
        # list_in_flight_with_evidence only returns active statuses anyway.
        grounded = self._incident(status=IncidentStatus.GROUNDED)
        grouped = stage_incidents([triaging, enriching, grounded])
        assert {g.id for g in grouped["triage"]} == {triaging.id}
        assert {g.id for g in grouped["enrichment"]} == {enriching.id}
        assert {g.id for g in grouped["intake"]} == {grounded.id}
        assert grouped["response"] == []


class TestExhaustiveness:
    def test_every_supervisor_disposition_is_terminal_mapped_or_excluded(self) -> None:
        # awaiting_approval_destructive is paired with the non-terminal AWAITING_APPROVAL
        # status (a parked state) — it is intentionally excluded from terminal counting.
        excluded = {supervisor.DISP_AWAITING_APPROVAL}
        all_dispositions = {
            value
            for name, value in vars(supervisor).items()
            if name.startswith("DISP_") and isinstance(value, str)
        }
        assert all_dispositions, "sanity check: supervisor module exposes DISP_* constants"

        unmapped = all_dispositions - excluded - set(pipeline_view._DISPOSITION_TO_TERMINAL_BRANCH)
        assert not unmapped, (
            f"Disposition(s) {unmapped} are emitted by the supervisor but missing from "
            "_DISPOSITION_TO_TERMINAL_BRANCH — they would be silently dropped from "
            "terminals.resolved/escalated. Add them to the map (or to `excluded` above "
            "if genuinely non-terminal)."
        )

    def test_every_active_incident_status_is_stage_mapped_or_terminal(self) -> None:
        terminal_statuses = {"resolved", "escalated", "failed"}
        unmapped = {
            status.value
            for status in IncidentStatus
            if status.value not in pipeline_view._STATUS_TO_STAGE
            and status.value not in terminal_statuses
        }
        assert not unmapped, (
            f"IncidentStatus value(s) {unmapped} are neither mapped to a pipeline-map "
            "stage nor in the known terminal set — they would be invisible on the rail."
        )
