"""KPI service — composes a KpiSnapshot from the incident repository."""

from __future__ import annotations

from datetime import UTC, datetime

from backend.domain.dashboard import KpiSnapshot
from backend.repositories.incidents import IncidentRepository


async def build_kpi_snapshot(repo: IncidentRepository) -> KpiSnapshot:
    """Compose a KpiSnapshot from aggregate reads — read-only, no mutation."""
    volume, disposition, mttd_ms, memory_hit = await _gather(repo)
    return KpiSnapshot(
        volume_over_time=volume,
        disposition_split=disposition,
        mean_time_to_disposition_ms=mttd_ms,
        memory_hit=memory_hit,
        generated_at=datetime.now(UTC),
    )


async def _gather(repo: IncidentRepository):
    # Must be sequential — asyncio.gather on the same async session causes
    # SQLAlchemy IllegalStateChangeError (concurrent use of one session).
    volume = await repo.kpi_volume_buckets()
    disposition = await repo.kpi_disposition_counts()
    mttd_ms = await repo.kpi_mean_time_to_disposition_ms()
    memory_hit = await repo.kpi_enriched_and_hit_counts()
    return volume, disposition, mttd_ms, memory_hit
