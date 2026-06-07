"""Integration tests — T016: Postgres trace store (testcontainer).

Spins a real Postgres; applies the trace_spans migration; drives a synthetic
incident through nested span() calls; reads back the tree.

Covers:
- Exactly one trace tree per incident with no orphans (SC-003)
- Spans persisted and queryable by correlation_id
- LLM spans carry tokens-in/out + model; missing usage → None ("unknown")
- trace_spans migration applies and rolls back cleanly (#1 SC-006 style)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from testcontainers.postgres import PostgresContainer

from backend.domain.telemetry import SpanKind, SpanStatus
from backend.infra.tracing import build_tracer, record_llm_usage, span

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        yield pg.get_connection_url().replace("psycopg2", "asyncpg").replace(
            "postgresql+asyncpg", "postgresql+asyncpg"
        )


@pytest.fixture(scope="module")
async def db_engine(pg_dsn):
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(pg_dsn, echo=False)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="module")
async def migrated_engine(db_engine):
    """Apply the trace_spans migration to the test Postgres instance."""
    from alembic import command
    from alembic.config import Config

    cfg = Config("config/alembic.ini")
    cfg.set_main_option("sqlalchemy.url", str(db_engine.url).replace("+asyncpg", ""))
    # Run synchronously — alembic is sync
    import asyncio
    loop = asyncio.get_event_loop()
    await asyncio.to_thread(command.upgrade, cfg, "head")
    yield db_engine
    await asyncio.to_thread(command.downgrade, cfg, "-1")


class TestTraceMigration:
    async def test_migration_up_and_down(self, pg_dsn) -> None:
        """trace_spans migration applies and rolls back cleanly."""
        from alembic import command
        from alembic.config import Config

        cfg = Config("config/alembic.ini")
        cfg.set_main_option("sqlalchemy.url", pg_dsn.replace("+asyncpg", ""))

        await asyncio.to_thread(command.upgrade, cfg, "head")
        # Verify the table exists
        from sqlalchemy import inspect, text
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(pg_dsn)
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE tablename='trace_spans'")
            )
            rows = result.fetchall()
        assert len(rows) == 1, "trace_spans table should exist after upgrade"

        await asyncio.to_thread(command.downgrade, cfg, "-1")
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE tablename='trace_spans'")
            )
            rows = result.fetchall()
        assert len(rows) == 0, "trace_spans table should be gone after downgrade"
        await engine.dispose()


class TestTraceStore:
    async def test_one_tree_no_orphans(self, migrated_engine) -> None:
        """A synthetic incident yields exactly one trace tree with no orphans (SC-003)."""
        from backend.infra.tracing import build_tracer, span
        from backend.infra.trace_repository import TraceRepository

        repo = TraceRepository(migrated_engine)
        tracer = build_tracer(exporter=repo, max_attr_bytes=8192)
        correlation_id = "inc_integration_001"

        with span(tracer, "root", SpanKind.ROOT, correlation_id=correlation_id) as root_s:
            with span(
                tracer, "triage.step", SpanKind.AGENT_STEP,
                correlation_id=correlation_id, parent_span_id=root_s.span_id
            ) as step_s:
                with span(
                    tracer, "llm.call", SpanKind.LLM_CALL,
                    correlation_id=correlation_id, parent_span_id=step_s.span_id
                ) as llm_s:
                    usage = MagicMock()
                    usage.prompt_tokens = 80
                    usage.completion_tokens = 40
                    record_llm_usage(llm_s, usage=usage, model="test-model")

        # Flush the exporter
        await repo.flush()

        tree = await repo.get_trace_tree(correlation_id)
        assert tree is not None
        all_spans = tree.all_spans()
        assert len(all_spans) == 3
        # Exactly one root
        roots = [s for s in all_spans if s.parent_span_id is None]
        assert len(roots) == 1
        # All non-root spans have resolvable parent ids
        span_ids = {s.span_id for s in all_spans}
        for s in all_spans:
            if s.parent_span_id is not None:
                assert s.parent_span_id in span_ids, f"Orphan: {s.span_id}"

    async def test_llm_span_tokens_persisted(self, migrated_engine) -> None:
        from backend.infra.tracing import build_tracer, span, record_llm_usage
        from backend.infra.trace_repository import TraceRepository

        repo = TraceRepository(migrated_engine)
        tracer = build_tracer(exporter=repo, max_attr_bytes=8192)
        correlation_id = "inc_llm_tokens_001"

        with span(tracer, "llm.call", SpanKind.LLM_CALL, correlation_id=correlation_id) as s:
            usage = MagicMock()
            usage.prompt_tokens = 120
            usage.completion_tokens = 60
            record_llm_usage(s, usage=usage, model="test-model-v2")

        await repo.flush()

        tree = await repo.get_trace_tree(correlation_id)
        llm_span = next(s for s in tree.all_spans() if s.kind == SpanKind.LLM_CALL)
        assert llm_span.tokens_in == 120
        assert llm_span.tokens_out == 60
        assert llm_span.llm_model == "test-model-v2"

    async def test_missing_usage_stored_as_none(self, migrated_engine) -> None:
        from backend.infra.tracing import build_tracer, span, record_llm_usage
        from backend.infra.trace_repository import TraceRepository

        repo = TraceRepository(migrated_engine)
        tracer = build_tracer(exporter=repo, max_attr_bytes=8192)
        correlation_id = "inc_unknown_usage_001"

        with span(tracer, "llm.call", SpanKind.LLM_CALL, correlation_id=correlation_id) as s:
            record_llm_usage(s, usage=None, model="some-model")

        await repo.flush()

        tree = await repo.get_trace_tree(correlation_id)
        llm_span = next(s for s in tree.all_spans() if s.kind == SpanKind.LLM_CALL)
        assert llm_span.tokens_in is None
        assert llm_span.tokens_out is None
