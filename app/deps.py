"""FastAPI dependencies for auth and DB session injection."""

from typing import Optional
from uuid import UUID
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.db import get_db
from app.utils.auth import decode_access_token

security = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    """Lightweight user context extracted from JWT. No DB lookup needed."""
    id: UUID
    org_id: UUID
    role: str


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> CurrentUser:
    """Extract and verify user from JWT Bearer token.

    This dependency is added to protected endpoints.
    When we switch to OAuth/Cognito, we just change how the token is verified
    (call Cognito instead of local JWT decode) but the CurrentUser interface stays the same.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(credentials.credentials)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return CurrentUser(
        id=UUID(payload["sub"]),
        org_id=UUID(payload["org_id"]),
        role=payload.get("role", "admin"),
    )


def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[CurrentUser]:
    """Same as get_current_user but returns None instead of 401.

    Used for endpoints that work both authenticated and unauthenticated (like health).
    """
    if not credentials:
        return None
    payload = decode_access_token(credentials.credentials)
    if not payload:
        return None
    return CurrentUser(
        id=UUID(payload["sub"]),
        org_id=UUID(payload["org_id"]),
        role=payload.get("role", "admin"),
    )
