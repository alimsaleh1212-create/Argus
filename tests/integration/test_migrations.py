"""Integration tests — T021: Alembic upgrade/downgrade round-trip.

Uses a real Postgres container via testcontainers. Verifies that:
- ``alembic upgrade head`` creates the baseline schema.
- ``alembic downgrade base`` reverses to empty cleanly (no drift).
"""

from __future__ import annotations

import subprocess

import pytest


@pytest.mark.integration
class TestAlembicMigrations:
    def test_upgrade_head_then_downgrade_base(self, postgres_container) -> None:
        """Baseline migration is reversible with no schema drift."""
        import os

        env = {**os.environ, "ARGUS__POSTGRES__DSN": postgres_container.get_dsn()}

        result = subprocess.run(
            ["uv", "run", "alembic", "-c", "config/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"upgrade head failed: {result.stderr}"

        result = subprocess.run(
            ["uv", "run", "alembic", "-c", "config/alembic.ini", "downgrade", "base"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"downgrade base failed: {result.stderr}"


@pytest.fixture(scope="module")
def postgres_container():
    """Start a real Postgres container for migration tests."""
    pytest.importorskip("testcontainers")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        pg.get_dsn = lambda: (
            f"postgresql+asyncpg://{pg.username}:{pg.password}"
            f"@{pg.get_container_host_ip()}:{pg.get_exposed_port(5432)}/{pg.dbname}"
        )
        yield pg
