"""Authentication utilities: JWT tokens and password hashing.

Designed for easy swap to OAuth/Cognito later:
- JWT creation/verification is isolated here
- Password hashing is only used for local auth
- When adding OAuth, just add a new auth provider path in the login endpoint
  that skips password verification and creates JWT from OAuth claims
"""

from datetime import datetime, timedelta
from typing import Optional
import uuid

import jwt
from passlib.context import CryptContext

from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def create_access_token(user_id: uuid.UUID, org_id: uuid.UUID, role: str, name: str = "") -> str:
    """Create a JWT access token.

    Payload includes user_id, org_id, and role so the backend can
    scope queries and check permissions without a DB lookup on every request.
    """
    expires = datetime.utcnow() + timedelta(hours=settings.jwt_expiry_hours)
    payload = {
        "sub": str(user_id),
        "org_id": str(org_id),
        "role": role,
        "name": name,
        "exp": expires,
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and verify a JWT access token.

    Returns the payload dict or None if invalid/expired.
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
