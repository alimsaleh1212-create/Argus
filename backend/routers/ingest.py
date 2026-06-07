"""Wazuh alert intake router — reserved (SPEC-ingestion #4).

RESERVED. Receipt is intentionally thin: validate → redact → persist → enqueue
→ 202 Accepted. The worker does the heavy lifting. Default integration is
Wazuh push-webhook (decided over worker-pull); a pull/cursor poller remains the
robust alternative if dropped alerts during downtime become a concern.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/ingest", tags=["ingest"])

# Reserved: POST /ingest/wazuh — implemented in SPEC-ingestion (#4).
