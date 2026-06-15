"""Serialize EvalReport → JSON and upload to MinIO (eval-reports bucket).

MinIO key scheme (FR-009, R7):
  reports/{commit_sha}/{run_id}.json      — always written
  freezes/{git_tag}/eval_report.json      — freeze runs only (copy)

History is retained: keys are unique, never overwritten.
Upload failure → caller sets verdict to incomplete (exit 3).
"""

from __future__ import annotations

import json

from backend.domain.eval import EvalReport
from backend.infra.config import EvalSettings


async def upload_report(
    report: EvalReport,
    cfg: EvalSettings,
    *,
    endpoint_url: str = "http://minio:9000",
    access_key: str = "minioadmin",
    secret_key: str = "minioadmin",
) -> None:
    """Upload report JSON to MinIO. Raises on failure (caller handles exit 3)."""
    import aioboto3

    body = report.model_dump_json(indent=2).encode()

    session = aioboto3.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )

    async with session.client("s3", endpoint_url=endpoint_url) as s3:
        # Ensure bucket exists
        try:
            await s3.head_bucket(Bucket=cfg.report_bucket)
        except Exception:
            await s3.create_bucket(Bucket=cfg.report_bucket)

        key = f"{cfg.report_prefix}/{report.commit_sha}/{report.run_id}.json"
        await s3.put_object(Bucket=cfg.report_bucket, Key=key, Body=body)

        # Freeze copy
        if report.git_tag:
            freeze_key = f"{cfg.freeze_prefix}/{report.git_tag}/eval_report.json"
            await s3.put_object(Bucket=cfg.report_bucket, Key=freeze_key, Body=body)


async def download_report(
    commit_sha: str,
    run_id: str,
    cfg: EvalSettings,
    *,
    endpoint_url: str = "http://minio:9000",
    access_key: str = "minioadmin",
    secret_key: str = "minioadmin",
) -> EvalReport:
    """Download and deserialize a report from MinIO."""
    import aioboto3

    session = aioboto3.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    key = f"{cfg.report_prefix}/{commit_sha}/{run_id}.json"
    async with session.client("s3", endpoint_url=endpoint_url) as s3:
        response = await s3.get_object(Bucket=cfg.report_bucket, Key=key)
        async with response["Body"] as stream:
            body = await stream.read()
    return EvalReport.model_validate_json(body)


def report_to_dict(report: EvalReport) -> dict:
    return json.loads(report.model_dump_json())
