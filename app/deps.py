"""FastAPI dependencies for auth and DB session injection."""

from typing import Optional
from uuid import UUID
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.db import get_db
from app.utils.auth import decode_access_token
from app.utils.cognito_auth import verify_cognito_token, cognito_claims_to_user_dict

security = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    """Lightweight user context extracted from JWT. No DB lookup needed."""
    id: UUID
    org_id: UUID
    role: str
    name: str = ""


def _resolve_token(token: str) -> Optional[dict]:
    """Try Cognito verification first, fall back to local JWT."""
    from app.config import settings
    if settings.cognito_user_pool_id:
        cognito_payload = verify_cognito_token(token)
        if cognito_payload:
            return cognito_claims_to_user_dict(cognito_payload)
    return decode_access_token(token)


def _provision_cognito_user(payload: dict, db: Session) -> dict:
    """Auto-create org+user in DB on first Cognito login if they don't exist yet."""
    from app.models.user import User
    from app.models.org import Org
    import uuid

    user_id = UUID(payload["sub"])
    user = db.get(User, user_id)
    if user:
        # Sync name/role in case Cognito attributes changed
        payload["org_id"] = str(user.org_id)
        payload["role"] = user.role
        payload["name"] = user.name
        return payload

    # First login — create org and user
    org = Org(name=f"{payload.get('name', 'My')} Org")
    db.add(org)
    db.flush()

    user = User(
        id=user_id,
        org_id=org.id,
        email=payload.get("email", f"{payload['sub']}@cognito"),
        name=payload.get("name", payload.get("username", "")),
        password_hash="",  # no local password for Cognito users
        role=payload.get("role", "admin"),
        auth_provider="cognito",
    )
    db.add(user)
    db.commit()

    payload["org_id"] = str(org.id)
    payload["role"] = user.role
    return payload


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> CurrentUser:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    from app.config import settings
    token = credentials.credentials
    payload = None

    if settings.cognito_user_pool_id:
        cognito_payload = verify_cognito_token(token)
        if cognito_payload:
            user_dict = cognito_claims_to_user_dict(cognito_payload)
            if user_dict:
                payload = _provision_cognito_user(user_dict, db)

    if payload is None:
        payload = decode_access_token(token)

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
        name=payload.get("name", ""),
    )


def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> Optional[CurrentUser]:
    if not credentials:
        return None

    from app.config import settings
    token = credentials.credentials
    payload = None

    if settings.cognito_user_pool_id:
        cognito_payload = verify_cognito_token(token)
        if cognito_payload:
            user_dict = cognito_claims_to_user_dict(cognito_payload)
            if user_dict:
                payload = _provision_cognito_user(user_dict, db)

    if payload is None:
        payload = decode_access_token(token)

    if not payload:
        return None

    return CurrentUser(
        id=UUID(payload["sub"]),
        org_id=UUID(payload["org_id"]),
        role=payload.get("role", "admin"),
        name=payload.get("name", ""),
    )
