"""
Auth primitives, deliberately built on pure-Python dependencies only.

Why not python-jose / bcrypt: python-jose's [cryptography] extra and the
`bcrypt` package (v4+) are both compiled (the latter is itself a Rust/PyO3
extension) — reintroducing the exact class of Termux install problem this
project just removed pydantic-core's sibling issue for for. We only need
HS256 (symmetric HMAC) JWTs for Phase 1, which stdlib `hmac`/`hashlib`
handles directly with zero extra dependencies. If asymmetric algorithms
(RS256/ES256) are needed later for federated/enterprise SSO, add `pyjwt`
with the `crypto` extra as a Production Deployment (non-Termux) dependency
at that time rather than carrying the compiled dependency now.
"""
import base64
import hashlib
import hmac
import json
import time

from passlib.context import CryptContext

from app.core.config import get_settings

settings = get_settings()

# pbkdf2_sha256: pure-Python (stdlib hashlib.pbkdf2_hmac under the hood),
# no compiled backend required. Swap to argon2 (via a Production Deployment
# dependency) if/when non-Termux hosts are the only target.
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_access_token(subject: str, tenant_id: str, extra_claims: dict | None = None) -> str:
    """Minimal, dependency-free HS256 JWT encoder."""
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": subject,
        "tenant_id": tenant_id,
        "exp": int(time.time()) + settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "iat": int(time.time()),
    }
    if extra_claims:
        payload.update(extra_claims)

    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()

    signature = hmac.new(settings.JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
    signature_b64 = _b64url_encode(signature)

    return f"{header_b64}.{payload_b64}.{signature_b64}"


def decode_access_token(token: str) -> dict | None:
    """Verifies signature and expiry; returns the payload dict, or None if invalid."""
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError:
        return None

    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected_sig = hmac.new(settings.JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()

    try:
        actual_sig = _b64url_decode(signature_b64)
    except Exception:
        return None

    if not hmac.compare_digest(expected_sig, actual_sig):
        return None

    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception:
        return None

    if payload.get("exp", 0) < time.time():
        return None

    return payload
