"""Auth request/response models (passwords never appear in responses)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    """User registration payload (role is always ``user``; see README)."""

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=72)


class LoginRequest(BaseModel):
    """Login payload (email + password)."""

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=72)


class RefreshRequest(BaseModel):
    """Refresh-token payload (rotates: the old token is revoked)."""

    refresh_token: str = Field(min_length=1)


class TokenResponse(BaseModel):
    """Issued token pair."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    """Public user representation (no password hash, ever)."""

    id: UUID
    email: str
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}
