"""Auth request/response models (passwords never appear in responses)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    """User registration payload (role is always ``user``; see README)."""

    email: str = Field(
        min_length=3,
        max_length=320,
        examples=["user_a@example.com"],
        description="Valid email address for the account",
    )
    password: str = Field(
        min_length=8,
        max_length=72,
        examples=["Password123!"],
        description="Account password (at least 8 characters)",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "email": "user_a@example.com",
                "password": "Password123!",
            }
        }
    }


class LoginRequest(BaseModel):
    """Login payload (email + password)."""

    email: str = Field(
        min_length=3,
        max_length=320,
        examples=["user_a@example.com"],
        description="Registered user email",
    )
    password: str = Field(
        min_length=1,
        max_length=72,
        examples=["Password123!"],
        description="User password",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "email": "user_a@example.com",
                "password": "Password123!",
            }
        }
    }


class RefreshRequest(BaseModel):
    """Refresh-token payload (rotates: the old token is revoked)."""

    refresh_token: str = Field(
        min_length=1,
        examples=["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMTExMTExMS0xMTExLTQxMTEtODExMS0xMTExMTExMTExMTEiLCJqdGkiOiIyMjIyMjIyMi0yMjIyLTQyMjItODIyMi0yMjIyMjIyMjIyMjIiLCJleHAiOjE4MDAwMDAwMDAsInR5cGUiOiJyZWZyZXNoIn0.signature"],
        description="Opaque or JWT refresh token string received during login",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMTExMTExMS0xMTExLTQxMTEtODExMS0xMTExMTExMTExMTEiLCJqdGkiOiIyMjIyMjIyMi0yMjIyLTQyMjItODIyMi0yMjIyMjIyMjIyMjIiLCJleHAiOjE4MDAwMDAwMDAsInR5cGUiOiJyZWZyZXNoIn0.signature"
            }
        }
    }


class TokenResponse(BaseModel):
    """Issued token pair."""

    access_token: str = Field(description="Bearer JWT access token used in Authorization header")
    refresh_token: str = Field(description="Refresh token used to obtain a new access token")
    token_type: str = Field(default="bearer", description="Token type, standard bearer")

    model_config = {
        "json_schema_extra": {
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMTExMTExMS0xMTExLTQxMTEtODExMS0xMTExMTExMTExMTEiLCJyb2xlIjoidXNlciIsImV4cCI6MTgwMDAwMDAwMCwidHlwZSI6ImFjY2VzcyJ9.signature",
                "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMTExMTExMS0xMTExLTQxMTEtODExMS0xMTExMTExMTExMTEiLCJqdGkiOiIyMjIyMjIyMi0yMjIyLTQyMjItODIyMi0yMjIyMjIyMjIyMjIiLCJleHAiOjE4MDAwMDAwMDAsInR5cGUiOiJyZWZyZXNoIn0.signature",
                "token_type": "bearer",
            }
        }
    }


class UserResponse(BaseModel):
    """Public user representation (no password hash, ever)."""

    id: UUID = Field(description="Unique user identifier")
    email: str = Field(description="User email address")
    role: str = Field(description="User role (user or admin)")
    created_at: datetime = Field(description="Account creation timestamp")

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": "11111111-1111-4111-8111-111111111111",
                "email": "user_a@example.com",
                "role": "user",
                "created_at": "2026-10-23T00:00:00Z",
            }
        },
    }
