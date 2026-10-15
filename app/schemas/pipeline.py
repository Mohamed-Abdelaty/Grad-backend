"""Pipeline and version API models."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class PipelineCreate(BaseModel):
    """Create-pipeline payload; ``config`` must be a valid pipeline document."""

    name: str = Field(min_length=1, max_length=256)
    config: dict[str, Any]


class PipelineUpdate(BaseModel):
    """Update-pipeline payload; a new immutable version is created."""

    name: str | None = Field(default=None, min_length=1, max_length=256)
    config: dict[str, Any] | None = None


class PipelineResponse(BaseModel):
    """Pipeline list/detail representation."""

    id: UUID
    name: str
    owner_id: UUID
    current_version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PipelineDetailResponse(PipelineResponse):
    """Pipeline detail including the current configuration snapshot."""

    config: dict[str, Any]


class PipelineVersionResponse(BaseModel):
    """One immutable configuration version."""

    version: int
    config: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}
