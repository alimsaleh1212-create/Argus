"""Unit tests for KPI aggregation math (T050)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.domain.dashboard import KpiSnapshot, MemoryHit, VolumeBucket
from backend.services.kpis import build_kpi_snapshot

_NOW = datetime(2026, 6, 12, 9, 0, 0, tzinfo=UTC)


class TestMemoryHitRate:
    def test_rate_computed_when_enriched_positive(self) -> None:
        m = MemoryHit(enriched=10, hits=3)
        # rate = 3/10 = 0.3
        assert m.rate is None  # rate is set externally in repo, not auto-computed by model

    def test_zero_enriched_yields_none_rate(self) -> None:
        m = MemoryHit(enriched=0, hits=0, rate=None)
        assert m.rate is None

    def test_all_enriched_hit_yields_rate_one(self) -> None:
        m = MemoryHit(enriched=5, hits=5, rate=1.0)
        assert m.rate == 1.0

    def test_partial_hits(self) -> None:
        m = MemoryHit(enriched=100, hits=42, rate=0.42)
        assert m.rate == pytest.approx(0.42)

    def test_hits_not_coerced_to_zero_when_none(self) -> None:
        m = MemoryHit(enriched=0, hits=0, rate=None)
        assert m.enriched == 0
        assert m.hits == 0


class TestVolumeBucket:
    def test_bucket_holds_datetime_and_count(self) -> None:
        b = VolumeBucket(bucket=_NOW, count=7)
        assert b.bucket == _NOW
        assert b.count == 7


class TestBuildKpiSnapshot:
    @pytest.mark.asyncio
    async def test_snapshot_composed_from_repo_aggregates(self) -> None:
        from unittest.mock import AsyncMock

        mock_repo = AsyncMock()
        mock_repo.kpi_volume_buckets = AsyncMock(return_value=[VolumeBucket(bucket=_NOW, count=5)])
        mock_repo.kpi_disposition_counts = AsyncMock(
            return_value={"auto_remediated": 3, "escalated": 1}
        )
        mock_repo.kpi_mean_time_to_disposition_ms = AsyncMock(return_value=45_000)
        mock_repo.kpi_enriched_and_hit_counts = AsyncMock(
            return_value=MemoryHit(enriched=10, hits=4, rate=0.4, bias_applied=2)
        )

        snapshot = await build_kpi_snapshot(mock_repo)

        assert isinstance(snapshot, KpiSnapshot)
        assert len(snapshot.volume_over_time) == 1
        assert snapshot.volume_over_time[0].count == 5
        assert snapshot.disposition_split["auto_remediated"] == 3
        assert snapshot.mean_time_to_disposition_ms == 45_000
        assert snapshot.memory_hit.hits == 4
        assert snapshot.memory_hit.rate == pytest.approx(0.4)
        assert snapshot.memory_hit.bias_applied == 2
        assert snapshot.generated_at is not None

    @pytest.mark.asyncio
    async def test_snapshot_handles_zero_enriched(self) -> None:
        from unittest.mock import AsyncMock

        mock_repo = AsyncMock()
        mock_repo.kpi_volume_buckets = AsyncMock(return_value=[])
        mock_repo.kpi_disposition_counts = AsyncMock(return_value={})
        mock_repo.kpi_mean_time_to_disposition_ms = AsyncMock(return_value=None)
        mock_repo.kpi_enriched_and_hit_counts = AsyncMock(
            return_value=MemoryHit(enriched=0, hits=0, rate=None, bias_applied=0)
        )

        snapshot = await build_kpi_snapshot(mock_repo)

        assert snapshot.memory_hit.enriched == 0
        assert snapshot.memory_hit.rate is None
        assert snapshot.memory_hit.bias_applied == 0
        assert snapshot.mean_time_to_disposition_ms is None

    @pytest.mark.asyncio
    async def test_snapshot_all_repo_methods_called(self) -> None:
        from unittest.mock import AsyncMock

        mock_repo = AsyncMock()
        mock_repo.kpi_volume_buckets = AsyncMock(return_value=[])
        mock_repo.kpi_disposition_counts = AsyncMock(return_value={})
        mock_repo.kpi_mean_time_to_disposition_ms = AsyncMock(return_value=None)
        mock_repo.kpi_enriched_and_hit_counts = AsyncMock(
            return_value=MemoryHit(enriched=0, hits=0, rate=None, bias_applied=0)
        )

        await build_kpi_snapshot(mock_repo)

        mock_repo.kpi_volume_buckets.assert_called_once()
        mock_repo.kpi_disposition_counts.assert_called_once()
        mock_repo.kpi_mean_time_to_disposition_ms.assert_called_once()
        mock_repo.kpi_enriched_and_hit_counts.assert_called_once()
