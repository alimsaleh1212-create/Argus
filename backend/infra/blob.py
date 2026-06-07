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
        # In production the access/secret are resolved from Vault secrets.
        # For the foundation, we read them from the Vault-resolved data if
        # available, or fall back to the provider-level override (tests).
        vault_client = getattr(settings, "_vault_client", None)
        if vault_client and settings.vault.required_paths:
            import json

            for path in settings.vault.required_paths:
                try:
                    raw = vault_client.get_secret(path)
                    data = json.loads(raw)
                    access_key = data.get("access_key", self._access_key or "minioadmin")
                    secret_key = data.get("secret_key", self._secret_key or "minioadmin")
                    break
                except Exception:
                    access_key = self._access_key or "minioadmin"
                    secret_key = self._secret_key or "minioadmin"
        else:
            access_key = self._access_key or "minioadmin"
            secret_key = self._secret_key or "minioadmin"

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
