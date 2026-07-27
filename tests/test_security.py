"""
Validates the stdlib-only JWT implementation that replaced python-jose,
and the pbkdf2-based password hashing that replaced bcrypt.
"""
import time
import pytest

from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_token_round_trip():
    token = create_access_token(subject="user-1", tenant_id="tenant-1")
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "user-1"
    assert payload["tenant_id"] == "tenant-1"


def test_token_rejects_tampered_signature():
    token = create_access_token(subject="user-1", tenant_id="tenant-1")
    header, payload, sig = token.split(".")
    tampered = f"{header}.{payload}.{sig[:-2]}xx"
    assert decode_access_token(tampered) is None


def test_token_rejects_expired():
    token = create_access_token(subject="user-1", tenant_id="tenant-1")
    # Forge an already-expired token using the same signing path
    import json
    from app.core.security import _b64url_decode, _b64url_encode
    import hmac, hashlib
    from app.core.config import get_settings

    header_b64, payload_b64, _ = token.split(".")
    payload = json.loads(_b64url_decode(payload_b64))
    payload["exp"] = int(time.time()) - 10
    new_payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{new_payload_b64}".encode()
    settings = get_settings()
    sig = hmac.new(settings.JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
    expired_token = f"{header_b64}.{new_payload_b64}.{_b64url_encode(sig)}"

    assert decode_access_token(expired_token) is None


def test_password_hash_round_trip():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)
