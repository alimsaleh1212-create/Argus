"""Pinned container images shared by integration tests.

MinIO stopped publishing images (Docker Hub `minio/minio` is gone; quay.io needs
login), so testcontainers' default `minio/minio:RELEASE.2022-12-02T19-19-22Z`
can no longer be pulled. `pgsty/minio` is a community-maintained drop-in build
(same server command, MINIO_ROOT_* env and S3 API); keep in sync with compose.yaml.
"""

MINIO_IMAGE = "pgsty/minio:RELEASE.2026-08-04T00-00-00Z"
