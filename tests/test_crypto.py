"""Test token encryption/decryption."""

from app.utils.crypto import encrypt_token, decrypt_token


def test_encrypt_decrypt_roundtrip():
    token = "ghp_abc123def456789"
    encrypted = encrypt_token(token)

    # Encrypted should not be the plaintext
    assert encrypted != token
    assert len(encrypted) > 0

    # Decrypt should return original
    decrypted = decrypt_token(encrypted)
    assert decrypted == token


def test_encrypt_empty_string():
    assert encrypt_token("") == ""
    assert decrypt_token("") == ""


def test_different_tokens_different_ciphertext():
    enc1 = encrypt_token("token-one")
    enc2 = encrypt_token("token-two")
    assert enc1 != enc2
