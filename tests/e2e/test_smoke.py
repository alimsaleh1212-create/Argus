"""E2E smoke test — T034: docker compose up from clean checkout.

Verifies:
- All services reach healthy within the grace window.
- GET /ready returns 200.
- docker compose down leaves no orphaned containers.

This test is expected to run in CI with Docker available.
It is marked ``e2e`` and skipped in environments without Docker.
"""

from __future__ import annotations

import os
import subprocess
import time

import httpx
import pytest


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


@pytest.mark.e2e
@pytest.mark.needs_compose
@pytest.mark.skipif(not _docker_available(), reason="Docker not available")
class TestComposeSmokeE2E:
    def test_compose_up_reaches_healthy(self) -> None:
        """Full stack comes up healthy and /ready returns 200."""
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        # Bring up the stack
        result = subprocess.run(
            ["docker", "compose", "up", "-d", "--wait", "--timeout", "120"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            pytest.fail(f"compose up failed:\n{result.stderr}")

        try:
            # Wait for /ready with retries
            deadline = time.time() + 60
            while time.time() < deadline:
                try:
                    resp = httpx.get("http://localhost:8000/ready", timeout=5.0)
                    if resp.status_code == 200:
                        body = resp.json()
                        assert body["ready"] is True
                        return
                except (httpx.ConnectError, httpx.TimeoutException):
                    pass
                time.sleep(2)
            pytest.fail("Stack did not reach /ready=200 within the grace window")
        finally:
            subprocess.run(
                ["docker", "compose", "down", "--volumes"],
                cwd=project_root,
                capture_output=True,
                timeout=60,
            )

    def test_compose_down_leaves_no_orphans(self) -> None:
        """After compose down, no project containers remain."""
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        subprocess.run(
            ["docker", "compose", "down", "--volumes", "--remove-orphans"],
            cwd=project_root,
            capture_output=True,
            timeout=60,
        )

        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.stdout.strip() in ("", "[]"), (
            f"Orphaned containers after down:\n{result.stdout}"
        )
