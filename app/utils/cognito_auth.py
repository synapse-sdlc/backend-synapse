"""Cognito JWT verification.

Verifies tokens issued by AWS Cognito using the pool's public JWKS endpoint.
Falls back gracefully when Cognito is not configured (COGNITO_USER_POOL_ID unset).
"""

import logging
from functools import lru_cache
from typing import Optional

import jwt
from jwt import PyJWKClient

from app.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_jwks_client() -> Optional[PyJWKClient]:
    """Cached JWKS client — fetches public keys once and reuses them."""
    if not settings.cognito_jwks_url:
        return None
    return PyJWKClient(settings.cognito_jwks_url, cache_keys=True)


def verify_cognito_token(token: str) -> Optional[dict]:
    """Verify a Cognito JWT and return its claims, or None if invalid."""
    client = _get_jwks_client()
    if not client:
        return None
    try:
        signing_key = client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            # Cognito access tokens don't set 'aud' — skip audience verification
            options={"verify_exp": True, "verify_aud": False},
        )
        return payload
    except jwt.ExpiredSignatureError:
        logger.debug("Cognito token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug("Cognito token invalid: %s", e)
        return None
    except Exception as e:
        logger.warning("Cognito verification error: %s", e)
        return None


def cognito_claims_to_user_dict(claims: dict) -> Optional[dict]:
    """Map Cognito token claims to our internal user dict format.

    Cognito access tokens use 'sub' as user ID.
    We use the custom:org_id attribute if present, otherwise fall back to sub.
    """
    sub = claims.get("sub")
    if not sub:
        return None
    return {
        "sub": sub,
        # Cognito access tokens carry username; id tokens carry email
        "name": claims.get("name") or claims.get("username") or claims.get("cognito:username", ""),
        "org_id": claims.get("custom:org_id") or sub,
        "role": claims.get("custom:role") or "developer",
    }
