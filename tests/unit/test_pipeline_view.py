"""Unit tests for the SOC pipeline-map data spine (M-a)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.domain.dashboard import (
    BranchOutflow,
    PipelineSnapshot,
    StageNode,
    TerminalCounts,
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


from unittest.mock import AsyncMock

from backend.services.pipeline_view import (
    STAGES,
    build_pipeline_snapshot,
    stage_branches,
    stage_in_flight,
    terminal_counts,
)


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
        flat = [b.to for stage in branches.values() for b in stage]
        assert all("unknown" not in label for label in flat)

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

        snap = await build_pipeline_snapshot(repo, window_hours=24)

        assert isinstance(snap, PipelineSnapshot)
        assert [s.key for s in snap.stages] == ["intake", "triage", "enrichment", "response"]
        triage = next(s for s in snap.stages if s.key == "triage")
        assert triage.in_flight == 4
        assert {b.to: b.count for b in triage.branches} == {"resolved": 2}
        assert snap.terminals.escalated == 1
        assert snap.terminals.awaiting == 1
        assert snap.window_hours == 24
        repo.status_counts.assert_called_once()
        repo.disposition_counts_since.assert_called_once_with(window_hours=24)
