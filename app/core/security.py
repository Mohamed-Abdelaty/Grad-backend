"""Password hashing (bcrypt) and JWT handling (PyJWT)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.config import settings

MAX_PASSWORD_BYTES = 72  # bcrypt limit; enforced at registration/login.


def hash_password(password: str) -> str:
    """Hash ``password`` with bcrypt. Never store or log plaintext."""
    raw = password.encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        raise ValueError("Password must be at most 72 bytes")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Verify ``password`` against a stored bcrypt hash (constant-time)."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _now() -> datetime:
    return datetime.now(UTC)


def create_access_token(*, user_id: str, role: str) -> str:
    """Create a short-lived JWT access token."""
    payload = {
        "sub": user_id,
        "role": role,
        "type": "access",
        "iat": _now(),
        "exp": _now() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(*, user_id: str) -> tuple[str, str]:
    """Create a refresh token; returns (token, jti) for server-side tracking."""
    jti = uuid.uuid4().hex
    payload = {
        "sub": user_id,
        "type": "refresh",
        "jti": jti,
        "iat": _now(),
        "exp": _now() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM), jti


def decode_token(token: str, *, expected_type: str) -> dict[str, object]:
    """Decode and validate a JWT; raises :class:`TokenError` on any problem."""
    try:
        payload: dict[str, object] = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"Invalid token: {exc}") from exc
    if payload.get("type") != expected_type:
        raise TokenError(f"Expected a {expected_type} token")
    if not payload.get("sub"):
        raise TokenError("Token has no subject")
    return payload


class TokenError(ValueError):
    """Raised for expired/invalid/wrong-type tokens."""
