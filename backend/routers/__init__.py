"""API router aggregation.

``api_router`` collects every sub-router. ``main.create_app`` includes this
single router, so adding an endpoint group later means writing the module and
adding one ``include_router`` line here — no change to ``main.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.dependencies import get_current_operator
from backend.routers import approvals, auth, health, incidents, ingest

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(ingest.router)

# Dashboard auth (unauthenticated by design — it IS the login endpoint)
api_router.include_router(auth.router)

# Dashboard read routes (all behind get_current_operator auth gate)
api_router.include_router(
    incidents.router,
    dependencies=[Depends(get_current_operator)],
)

# Human-in-the-loop approval routes (behind auth gate; actor replaced in T032)
api_router.include_router(
    approvals.router,
    dependencies=[Depends(get_current_operator)],
)
