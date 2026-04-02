"""Token encryption/decryption using Fernet symmetric encryption.

Tokens are encrypted at rest in the database. The encryption key
comes from settings (env var ENCRYPTION_KEY). In production this
would be stored in AWS Secrets Manager.
"""

import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


def _get_fernet() -> Fernet:
    """Derive a valid Fernet key from the config encryption_key.

    The config key can be any string. We hash it to get exactly 32 bytes
    then base64 encode it for Fernet.
    """
    key_bytes = hashlib.sha256(settings.encryption_key.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(fernet_key)


def encrypt_token(token: str) -> str:
    """Encrypt a plaintext token. Returns a base64-encoded encrypted string."""
    if not token:
        return ""
    f = _get_fernet()
    return f.encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    """Decrypt an encrypted token. Returns plaintext."""
    if not encrypted:
        return ""
    f = _get_fernet()
    try:
        return f.decrypt(encrypted.encode()).decode()
    except InvalidToken:
        raise ValueError("Failed to decrypt token. Encryption key may have changed.")
