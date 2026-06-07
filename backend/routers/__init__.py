"""API router aggregation.

``api_router`` collects every sub-router. ``main.create_app`` includes this
single router, so adding an endpoint group later means writing the module and
adding one ``include_router`` line here — no change to ``main.py``.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.routers import health

api_router = APIRouter()
api_router.include_router(health.router)

# Reserved — wired by their owning specs:
# from backend.routers import ingest, incidents, approvals
# api_router.include_router(ingest.router)      # SPEC-ingestion (#4): Wazuh webhook
# api_router.include_router(incidents.router)   # incident read/timeline
# api_router.include_router(approvals.router)   # human-in-the-loop approval
