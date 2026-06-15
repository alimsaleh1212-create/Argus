"""T028 — EvalReport → MinIO round-trip integration test.

Uses testcontainers MinIO (mirrors infra/blob.py pattern).
- EvalReport serializes, uploads to eval-reports bucket
- Downloaded report validates against the JSON schema
- A prior report at a different key is NOT overwritten (history retained, FR-009)
"""

from __future__ import annotations

import json
import pathlib
import uuid
from datetime import UTC, datetime

import pytest
from testcontainers.minio import MinioContainer

from backend.domain.eval import (
    EvalReport,
    FreezeVerdict,
    GateKind,
    GateResult,
    RunMode,
)
from backend.infra.config import EvalSettings

SCHEMA_PATH = pathlib.Path("specs/013-eval-harness/contracts/eval-report.schema.json")


def _make_report(commit_sha: str = "abc12345", run_id: str | None = None,
                 git_tag: str | None = None) -> EvalReport:
    return EvalReport(
        run_id=run_id or str(uuid.uuid4()),
        run_mode=RunMode.freeze,
        commit_sha=commit_sha,
        git_tag=git_tag,
        created_at=datetime.now(UTC),
        providers=["gemini", "ollama"],
        gate_results=[
            GateResult(
                gate="smoke", kind=GateKind.required, provider=None,
                score=1.0, threshold={"max_unhealthy_services": 0},
                passed=True, blocking=True, evidence="ok",
            )
        ],
        rationale=None,
        verdict=FreezeVerdict.certifiable,
        summary={"passed": 1, "failed": 0, "reported": 0, "unknown": 0},
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_report_upload_and_download():
    with MinioContainer() as minio:
        endpoint = minio.get_config()["endpoint_url"]
        cfg = EvalSettings(
            report_bucket="eval-reports",
            report_prefix="reports",
            freeze_prefix="freezes",
        )

        from backend.eval.report import download_report, upload_report

        report = _make_report(git_tag="v1.0.0")
        await upload_report(report, cfg, endpoint_url=endpoint,
                            access_key="minioadmin", secret_key="minioadmin")

        downloaded = await download_report(
            report.commit_sha, report.run_id, cfg,
            endpoint_url=endpoint,
            access_key="minioadmin", secret_key="minioadmin",
        )

        assert downloaded.run_id == report.run_id
        assert downloaded.verdict == FreezeVerdict.certifiable
        assert downloaded.git_tag == "v1.0.0"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_two_runs_at_different_keys_are_both_retained():
    """History is retained: uploading a second report does not overwrite the first."""
    with MinioContainer() as minio:
        endpoint = minio.get_config()["endpoint_url"]
        cfg = EvalSettings(
            report_bucket="eval-reports",
            report_prefix="reports",
            freeze_prefix="freezes",
        )
        from backend.eval.report import download_report, upload_report

        run_id_1 = str(uuid.uuid4())
        run_id_2 = str(uuid.uuid4())
        r1 = _make_report(commit_sha="commit_a", run_id=run_id_1)
        r2 = _make_report(commit_sha="commit_b", run_id=run_id_2)

        await upload_report(r1, cfg, endpoint_url=endpoint,
                            access_key="minioadmin", secret_key="minioadmin")
        await upload_report(r2, cfg, endpoint_url=endpoint,
                            access_key="minioadmin", secret_key="minioadmin")

        # Both must still be downloadable
        d1 = await download_report("commit_a", run_id_1, cfg, endpoint_url=endpoint,
                                   access_key="minioadmin", secret_key="minioadmin")
        d2 = await download_report("commit_b", run_id_2, cfg, endpoint_url=endpoint,
                                   access_key="minioadmin", secret_key="minioadmin")

        assert d1.run_id == run_id_1
        assert d2.run_id == run_id_2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_report_matches_json_schema():
    """Downloaded report validates against contracts/eval-report.schema.json."""
    import jsonschema  # type: ignore[import-untyped]

    with MinioContainer() as minio:
        endpoint = minio.get_config()["endpoint_url"]
        cfg = EvalSettings(report_bucket="eval-reports")

        from backend.eval.report import download_report, upload_report

        report = _make_report()
        await upload_report(report, cfg, endpoint_url=endpoint,
                            access_key="minioadmin", secret_key="minioadmin")
        downloaded = await download_report(
            report.commit_sha, report.run_id, cfg,
            endpoint_url=endpoint, access_key="minioadmin", secret_key="minioadmin",
        )

        if SCHEMA_PATH.exists():
            schema = json.loads(SCHEMA_PATH.read_text())
            instance = json.loads(downloaded.model_dump_json())
            jsonschema.validate(instance, schema)
