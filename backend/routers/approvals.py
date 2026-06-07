"""Human-in-the-loop approval router — reserved.

RESERVED. Endpoints for an operator to approve/reject a pending response action;
approval resumes the paused supervisor graph (#5). Implemented by its owning spec.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/approvals", tags=["approvals"])
