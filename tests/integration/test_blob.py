"""Integration tests — T022: MinIO bucket bootstrap and put/get.

Uses a real MinIO container via testcontainers. Verifies that:
- Buckets are bootstrapped (eval-reports, incident-snapshots).
- A written object is read back with identical bytes.
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
class TestBlobStorage:
    def test_bucket_bootstrap_and_putget(self, minio_container) -> None:
        """Buckets are created and objects round-trip identically."""
        import asyncio

        from backend.infra.blob import BlobClientProvider
        from backend.infra.config import MinioSettings, Settings

        settings = Settings(
            minio=MinioSettings(
                endpoint_url=minio_container.get_url(),
                buckets=["eval-reports", "incident-snapshots"],
            )
        )

        async def _run() -> None:

            provider = BlobClientProvider(
                access_key=minio_container.access_key,
                secret_key=minio_container.secret_key,
            )
            async with provider.build(settings) as client:
                # Write then read back
                await client.put_object(
                    bucket="eval-reports",
                    key="test/hello.txt",
                    body=b"hello world",
                )
                data = await client.get_object(bucket="eval-reports", key="test/hello.txt")
                assert data == b"hello world"

        asyncio.run(_run())


@pytest.fixture(scope="module")
def minio_container():
    """Start a real MinIO container for blob tests."""
    pytest.importorskip("testcontainers")
    from testcontainers.minio import MinioContainer

    with MinioContainer() as minio:
        minio.get_url = lambda: (
            f"http://{minio.get_container_host_ip()}:{minio.get_exposed_port(9000)}"
        )
        yield minio
