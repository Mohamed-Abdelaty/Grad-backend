"""Authentication / authorization dependencies (backend-enforced)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.core.security import TokenError, decode_token
from app.db.session import get_db
from app.models.entities import User

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the JWT access token to an active user (401 otherwise)."""
    if credentials is None or not credentials.credentials:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "unauthorized", "Missing access token")
    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except TokenError as exc:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "unauthorized", str(exc)) from exc
    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except ValueError as exc:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED, "unauthorized", "Invalid token subject"
        ) from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "unauthorized", "User not found or inactive")
    return user


def require_admin(current: User = Depends(get_current_user)) -> User:
    """Require the ``admin`` role (403 otherwise)."""
    if current.role != "admin":
        raise ApiError(status.HTTP_403_FORBIDDEN, "forbidden", "Admin role required")
    return current


def owner_filter(model_or_owner_col: Any, user: User) -> Any:
    """Return a SQLAlchemy filter clause restricting rows to user (None for admin)."""
    if user.role == "admin":
        return None
    col = getattr(model_or_owner_col, "owner_id", model_or_owner_col)
    return col == user.id
