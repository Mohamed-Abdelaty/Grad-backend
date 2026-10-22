"""API Key management endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_principal
from app.core.errors import ApiError
from app.db.session import get_db
from app.schemas.apikey import (
    ApiKeyCreate,
    ApiKeyCreateResponse,
    ApiKeyListResponse,
    ApiKeyRevokeRequest,
)
from app.services.apikey import ApiKeyService, Principal

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


@router.post(
    "",
    response_model=ApiKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_api_key(
    payload: ApiKeyCreate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ApiKeyCreateResponse:
    """Create a new API key. Raw key returned ONCE in response."""
    # API keys cannot manage other API keys
    if principal.auth_method == "apikey":
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "forbidden",
            "API keys cannot create other API keys",
        )
    user = principal.user
    return ApiKeyService.create_key(db, user, payload, request.client.host if request.client else None)


@router.get("", response_model=ApiKeyListResponse)
def list_api_keys(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ApiKeyListResponse:
    """List API keys for the current user (no raw keys)."""
    # API keys cannot list API keys
    if principal.auth_method == "apikey":
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "forbidden",
            "API keys cannot list API keys",
        )
    user = principal.user
    return ApiKeyService.list_keys(db, user)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(
    key_id: UUID,
    payload: ApiKeyRevokeRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> None:
    """Revoke an API key."""
    # API keys cannot revoke API keys
    if principal.auth_method == "apikey":
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "forbidden",
            "API keys cannot revoke API keys",
        )
    if not payload.confirm:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "validation_error", "Confirmation required")
    user = principal.user
    ApiKeyService.revoke_key(db, user, key_id, request.client.host if request.client else None)
