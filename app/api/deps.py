"""Authentication / authorization dependencies (backend-enforced)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Depends, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.core.security import TokenError, decode_token
from app.db.session import get_db
from app.models.entities import User
from app.services.apikey import ApiKeyService, Principal

bearer_scheme = HTTPBearer(auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    x_api_key: str | None = Depends(api_key_header),
    db: Session = Depends(get_db),
) -> Principal:
    """Unified auth: accepts JWT (Authorization: Bearer) OR API Key (X-API-Key).
    Returns a Principal with user, scopes, and auth_method.
    """
    # Try JWT first
    if credentials and credentials.credentials:
        try:
            payload = decode_token(credentials.credentials, expected_type="access")
            user_id = uuid.UUID(str(payload["sub"]))
            user = db.get(User, user_id)
            if user and user.is_active:
                # JWT users get all scopes allowed by their role
                scopes = _role_scopes(user.role)
                return Principal(user=user, scopes=scopes, auth_method="jwt")
        except (TokenError, ValueError, KeyError):
            pass  # Fall through to API key

    # Try API Key
    if x_api_key:
        result = ApiKeyService.authenticate_key(db, x_api_key)
        if result:
            user, scopes = result
            return Principal(user=user, scopes=scopes, auth_method="apikey")

    raise ApiError(status.HTTP_401_UNAUTHORIZED, "unauthorized", "Missing or invalid credentials")


def get_current_user(
    principal: Principal = Depends(get_current_principal),
) -> User:
    """Resolve unified auth (JWT or API Key) to an active user."""
    return principal.user


def get_current_user_from_principal(principal: Principal = Depends(get_current_principal)) -> User:
    """Dependency that extracts the User from a Principal (for backward compatibility)."""
    return principal.user


def require_admin(principal: Principal = Depends(get_current_principal)) -> User:
    """Require the ``admin`` role and reject API keys (API keys cannot call admin endpoints)."""
    if principal.auth_method == "apikey":
        raise ApiError(status.HTTP_403_FORBIDDEN, "forbidden", "API keys cannot access admin endpoints")
    if principal.user.role != "admin":
        raise ApiError(status.HTTP_403_FORBIDDEN, "forbidden", "Admin role required")
    return principal.user


def require_scope(scope: str):
    """Dependency factory that requires a specific scope."""
    def _check_scope(principal: Principal = Depends(get_current_principal)) -> Principal:
        if not principal.has_scope(scope):
            raise ApiError(
                status.HTTP_403_FORBIDDEN,
                "insufficient_scope",
                f"Required scope: {scope}",
                {"required": scope, "granted": principal.scopes},
            )
        return principal
    return _check_scope


def _role_scopes(role: str) -> list[str]:
    """Map roles to default scopes."""
    if role == "admin":
        return ["read", "run", "write"]
    return ["read", "run", "write"]  # Users get all scopes by default for JWT


def owner_filter(model_or_owner_col: Any, user: User) -> Any:
    """Return a SQLAlchemy filter clause restricting rows to user (None for admin)."""
    if user.role == "admin":
        return None
    col = getattr(model_or_owner_col, "owner_id", model_or_owner_col)
    return col == user.id
