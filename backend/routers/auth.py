"""Auth router — POST /auth/login exchanges credentials for a session token.

No authentication required on this router (it IS the login endpoint).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.dependencies import get_auth_service
from backend.domain.dashboard import LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    auth_service=Depends(get_auth_service),
) -> TokenResponse:
    """Exchange admin credentials for a short-lived HS256 session token.

    Always returns a generic 401 on failure — no user enumeration.
    """
    if not auth_service.verify_credentials(body.username, body.password.get_secret_value()):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token, expires_in = auth_service.issue_token(subject=body.username, role="admin")
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in,
        role="admin",
    )
