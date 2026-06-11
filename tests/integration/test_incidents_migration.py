"""Integration test — T009: incidents migration round-trip.

Verifies upgrade creates the incidents table + indexes;
downgrade drops them cleanly.
"""

from __future__ import annotations

import os
import subprocess

import pytest


@pytest.mark.integration
class TestIncidentsMigration:
    def test_upgrade_creates_incidents_table(self, pg_container) -> None:
        env = {**os.environ, "ARGUS__POSTGRES__DSN": pg_container.get_dsn()}
        result = subprocess.run(
            ["uv", "run", "alembic", "-c", "config/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"upgrade head failed: {result.stderr}"

        import asyncio

        import asyncpg

        async def check() -> list[str]:
            dsn = pg_container.get_dsn().replace("+asyncpg", "")
            conn = await asyncpg.connect(dsn)
            try:
                rows = await conn.fetch(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                )
                return [r["tablename"] for r in rows]
            finally:
                await conn.close()

        tables = asyncio.run(check())
        assert "incidents" in tables

    def test_downgrade_drops_incidents_table(self, pg_container) -> None:
        env = {**os.environ, "ARGUS__POSTGRES__DSN": pg_container.get_dsn()}

        subprocess.run(
            ["uv", "run", "alembic", "-c", "config/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
        )

        result = subprocess.run(
            ["uv", "run", "alembic", "-c", "config/alembic.ini", "downgrade", "base"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"downgrade base failed: {result.stderr}"

        import asyncio

        import asyncpg

        async def check() -> list[str]:
            dsn = pg_container.get_dsn().replace("+asyncpg", "")
            conn = await asyncpg.connect(dsn)
            try:
                rows = await conn.fetch(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                )
                return [r["tablename"] for r in rows]
            finally:
                await conn.close()

        tables = asyncio.run(check())
        assert "incidents" not in tables


@pytest.fixture(scope="module")
def pg_container():
    pytest.importorskip("testcontainers")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        pg.get_dsn = lambda: (
            f"postgresql+asyncpg://{pg.username}:{pg.password}"
            f"@{pg.get_container_host_ip()}:{pg.get_exposed_port(5432)}/{pg.dbname}"
        )
        yield pg
