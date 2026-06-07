"""Integration tests — T039: pre-commit blocks bad commits.

Verifies that:
- A planted ruff violation is caught by ``pre-commit run ruff``.
- A planted gitleaks-detectable fake secret is caught by ``pre-commit run gitleaks``.

These tests are skipped if pre-commit is not installed.
"""

from __future__ import annotations

import os
import subprocess
import tempfile

import pytest


def _pre_commit_available() -> bool:
    result = subprocess.run(
        ["pre-commit", "--version"],
        capture_output=True,
        timeout=10,
    )
    return result.returncode == 0


@pytest.mark.integration
@pytest.mark.skipif(not _pre_commit_available(), reason="pre-commit not installed")
class TestPreCommitGates:
    def test_ruff_blocks_lint_violation(self) -> None:
        """pre-commit ruff hook rejects files with lint violations."""
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            # Intentional lint violation: unused import
            f.write("import os\n")
            tmp_path = f.name

        try:
            result = subprocess.run(
                ["pre-commit", "run", "ruff", "--files", tmp_path],
                capture_output=True,
                text=True,
            )
            # ruff should flag the unused import → exit non-zero
            assert result.returncode != 0, "Expected ruff to flag unused import"
        finally:
            os.unlink(tmp_path)

    def test_gitleaks_blocks_fake_secret(self) -> None:
        """pre-commit gitleaks hook rejects files containing credential-like patterns."""
        with tempfile.NamedTemporaryFile(suffix=".env", mode="w", delete=False, dir="/tmp") as f:
            # gitleaks pattern: AWS-style key
            f.write("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n")
            tmp_path = f.name

        try:
            result = subprocess.run(
                ["pre-commit", "run", "gitleaks", "--files", tmp_path],
                capture_output=True,
                text=True,
            )
            assert result.returncode != 0, "Expected gitleaks to detect fake secret"
        finally:
            os.unlink(tmp_path)
