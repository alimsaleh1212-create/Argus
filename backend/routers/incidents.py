"""Incident read/timeline router — reserved.

RESERVED. Read-side endpoints for incidents and their enrichment/response
timeline, consumed by the dashboard (#12). Implemented by its owning spec.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/incidents", tags=["incidents"])
