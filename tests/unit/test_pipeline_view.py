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
