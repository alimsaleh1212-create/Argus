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

from unittest.mock import MagicMock

import pytest
from testcontainers.postgres import PostgresContainer

from backend.domain.telemetry import SpanKind
from backend.infra.tracing import build_tracer, record_llm_usage, span

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        yield (
            pg.get_connection_url()
            .replace("psycopg2", "asyncpg")
            .replace("postgresql+asyncpg", "postgresql+asyncpg")
        )


@pytest.fixture(scope="module")
async def db_engine(pg_dsn):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    # NullPool prevents connection reuse across tests so asyncpg never tries
    # to cancel/close a connection on an already-closed function-scoped event loop.
    engine = create_async_engine(pg_dsn, echo=False, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="module")
async def migrated_engine(db_engine):
    """Apply the trace_spans migration to the test Postgres instance."""
    import os
    import subprocess

    # Run alembic as a subprocess (same as test_migrations.py) so asyncpg
    # is used end-to-end and DNS resolution is not affected by the event loop.
    env = {
        **os.environ,
        "ARGUS__POSTGRES__DSN": db_engine.url.render_as_string(hide_password=False),
    }
    result = subprocess.run(
        ["uv", "run", "alembic", "-c", "config/alembic.ini", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}")
    yield db_engine
    # No teardown needed — the testcontainer is destroyed at module end.


class TestTraceMigration:
    async def test_migration_up_and_down(self, pg_dsn) -> None:
        """trace_spans migration (0002) applies and rolls back cleanly."""
        import os
        import subprocess

        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        # Run alembic as subprocess so asyncpg is used and DNS works in threads.
        # Target 0002 specifically (trace_spans) so the test remains focused.
        env = {**os.environ, "ARGUS__POSTGRES__DSN": pg_dsn}

        subprocess.run(
            ["uv", "run", "alembic", "-c", "config/alembic.ini", "upgrade", "0002"],
            env=env,
            check=True,
        )
        engine = create_async_engine(pg_dsn)
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE tablename='trace_spans'")
            )
            rows = result.fetchall()
        assert len(rows) == 1, "trace_spans table should exist after upgrade to 0002"

        subprocess.run(
            ["uv", "run", "alembic", "-c", "config/alembic.ini", "downgrade", "0001"],
            env=env,
            check=True,
        )
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE tablename='trace_spans'")
            )
            rows = result.fetchall()
        assert len(rows) == 0, "trace_spans table should be gone after downgrade to 0001"
        await engine.dispose()


class TestTraceStore:
    async def test_one_tree_no_orphans(self, migrated_engine) -> None:
        """A synthetic incident yields exactly one trace tree with no orphans (SC-003)."""
        from backend.infra.trace_repository import TraceRepository

        repo = TraceRepository(migrated_engine)
        tracer = build_tracer(exporter=repo, max_attr_bytes=8192)
        correlation_id = "inc_integration_001"

        with span(tracer, "root", SpanKind.ROOT, correlation_id=correlation_id) as root_s:
            with span(
                tracer,
                "triage.step",
                SpanKind.AGENT_STEP,
                correlation_id=correlation_id,
                parent_span_id=root_s.span_id,
            ) as step_s:
                with span(
                    tracer,
                    "llm.call",
                    SpanKind.LLM_CALL,
                    correlation_id=correlation_id,
                    parent_span_id=step_s.span_id,
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
        from backend.infra.trace_repository import TraceRepository
        from backend.infra.tracing import record_llm_usage

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
        from backend.infra.trace_repository import TraceRepository
        from backend.infra.tracing import record_llm_usage

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
