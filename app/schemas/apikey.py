"""API Key API models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ApiKeyScope(str):
    READ = "read"
    RUN = "run"
    WRITE = "write"


class ApiKeyCreate(BaseModel):
    """Create API key payload."""

    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "example": {
                "name": "ci-runner-key",
                "scopes": ["read", "run"],
                "expires_at": None,
            }
        },
    }

    name: str = Field(min_length=1, max_length=128, examples=["ci-runner-key"])
    scopes: list[Literal["read", "run", "write"]] = Field(min_length=1, examples=[["read", "run"]])
    expires_at: datetime | None = Field(default=None, examples=[None])


class ApiKeyCreateResponse(BaseModel):
    """API key creation response (raw key returned ONCE)."""

    id: UUID = Field(examples=["99999999-9999-4999-8999-999999999999"])
    name: str = Field(examples=["ci-runner-key"])
    prefix: str = Field(examples=["a1b2c3d4"])
    key: str = Field(description="Raw API key — copy immediately; never returned again!", examples=["cf_a1b2c3d4_8f90ab12cd34ef56ab78cd90ef12ab34"])
    scopes: list[str] = Field(examples=[["read", "run"]])
    expires_at: datetime | None = Field(default=None, examples=[None])
    created_at: datetime

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": "99999999-9999-4999-8999-999999999999",
                "name": "ci-runner-key",
                "prefix": "a1b2c3d4",
                "key": "cf_a1b2c3d4_8f90ab12cd34ef56ab78cd90ef12ab34",
                "scopes": ["read", "run"],
                "expires_at": None,
                "created_at": "2026-10-23T00:00:00Z",
            }
        },
    }


class ApiKeyResponse(BaseModel):
    """API key representation (NO raw key, only hash stored)."""

    id: UUID = Field(examples=["99999999-9999-4999-8999-999999999999"])
    name: str = Field(examples=["ci-runner-key"])
    prefix: str = Field(examples=["a1b2c3d4"])
    scopes: list[str] = Field(examples=[["read", "run"]])
    expires_at: datetime | None = Field(default=None, examples=[None])
    last_used_at: datetime | None = Field(default=None, examples=["2026-10-23T00:05:00Z"])
    revoked_at: datetime | None = Field(default=None, examples=[None])
    created_at: datetime

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": "99999999-9999-4999-8999-999999999999",
                "name": "ci-runner-key",
                "prefix": "a1b2c3d4",
                "scopes": ["read", "run"],
                "expires_at": None,
                "last_used_at": "2026-10-23T00:05:00Z",
                "revoked_at": None,
                "created_at": "2026-10-23T00:00:00Z",
            }
        },
    }


class ApiKeyListResponse(BaseModel):
    """Paginated API keys list."""

    items: list[ApiKeyResponse]
    total: int = Field(examples=[1])
    page: int = Field(examples=[1])
    page_size: int = Field(examples=[20])

    model_config = {
        "json_schema_extra": {
            "example": {
                "items": [
                    {
                        "id": "99999999-9999-4999-8999-999999999999",
                        "name": "ci-runner-key",
                        "prefix": "a1b2c3d4",
                        "scopes": ["read", "run"],
                        "expires_at": None,
                        "last_used_at": None,
                        "revoked_at": None,
                        "created_at": "2026-10-23T00:00:00Z",
                    }
                ],
                "total": 1,
                "page": 1,
                "page_size": 20,
            }
        }
    }


class ApiKeyRevokeRequest(BaseModel):
    """Revoke API key request."""

    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "example": {
                "confirm": True
            }
        },
    }

    confirm: bool = Field(default=True, examples=[True])
