"""Async MinIO/S3 client (aioboto3) and BlobClientProvider."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator
from typing import Any

import aioboto3

from backend.infra.logging import get_logger

logger = get_logger(__name__)


class BlobClient:
    """Thin wrapper around an aioboto3 S3 resource with bootstrap helpers."""

    def __init__(self, session: aioboto3.Session, endpoint_url: str, buckets: list[str]) -> None:
        self._session = session
        self._endpoint_url = endpoint_url
        self._buckets = buckets

    async def bootstrap_buckets(self) -> None:
        """Create configured buckets if they don't already exist."""
        async with self._session.client("s3", endpoint_url=self._endpoint_url) as s3:
            for bucket in self._buckets:
                try:
                    await s3.head_bucket(Bucket=bucket)
                    logger.info("bucket_exists", bucket=bucket)
                except Exception:
                    await s3.create_bucket(Bucket=bucket)
                    logger.info("bucket_created", bucket=bucket)

    async def put_object(self, bucket: str, key: str, body: bytes) -> None:
        async with self._session.client("s3", endpoint_url=self._endpoint_url) as s3:
            await s3.put_object(Bucket=bucket, Key=key, Body=body)

    async def get_object(self, bucket: str, key: str) -> bytes:
        async with self._session.client("s3", endpoint_url=self._endpoint_url) as s3:
            response = await s3.get_object(Bucket=bucket, Key=key)
            async with response["Body"] as stream:
                return await stream.read()


class BlobClientProvider:
    name = "blob_client"

    def __init__(
        self,
        access_key: str | None = None,
        secret_key: str | None = None,
    ) -> None:
        # These are injected for tests against a real MinIO container.
        # In production they come from Vault-resolved secrets in Settings.
        self._access_key = access_key
        self._secret_key = secret_key

    @contextlib.asynccontextmanager
    async def build(self, settings: Any) -> AsyncGenerator[BlobClient, None]:
        # Read MinIO credentials from the already-resolved Vault singleton at the
        # explicit secret/minio path. Falls back to the provider-level override
        # (used by tests, which build without a Vault container).
        access_key = self._access_key or "minioadmin"
        secret_key = self._secret_key or "minioadmin"
        container = getattr(settings, "_container", None)
        vault_client = getattr(container, "vault_client", None) if container else None
        if vault_client is not None:
            try:
                data = vault_client.get_secret("secret/minio")
                access_key = data.get("access_key", access_key)
                secret_key = data.get("secret_key", secret_key)
            except Exception as exc:
                logger.warning("blob_vault_credentials_unavailable", error=str(exc))

        session = aioboto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

        client = BlobClient(
            session=session,
            endpoint_url=settings.minio.endpoint_url,
            buckets=settings.minio.buckets,
        )

        await client.bootstrap_buckets()
        logger.info("blob_client_ready", buckets=settings.minio.buckets)

        yield client
        logger.info("blob_client_disposed")


def register_blob_provider() -> None:
    from backend.infra.container import register_provider

    register_provider(BlobClientProvider())
