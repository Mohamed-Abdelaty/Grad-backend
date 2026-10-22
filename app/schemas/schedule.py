"""Schedule API models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ScheduleCreate(BaseModel):
    """Create-schedule payload."""

    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "example": {
                "cron": "0 * * * *",
                "timezone": "UTC",
                "enabled": True,
                "start_at": None,
                "end_at": None,
                "misfire_policy": "skip",
            }
        },
    }

    cron: str = Field(min_length=1, max_length=128, description="5-field cron expression", examples=["0 * * * *"])
    timezone: str = Field(min_length=1, max_length=64, description="IANA timezone identifier", examples=["UTC"])
    enabled: bool = Field(default=True, description="Whether schedule is active", examples=[True])
    start_at: datetime | None = Field(default=None, description="Optional activation timestamp", examples=[None])
    end_at: datetime | None = Field(default=None, description="Optional expiration timestamp", examples=[None])
    misfire_policy: Literal["skip", "run_once"] = Field(
        default="skip",
        description="Policy for missed intervals (skip or run_once)",
        examples=["skip"],
    )


class ScheduleUpdate(BaseModel):
    """Update-schedule payload."""

    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "example": {
                "cron": "*/30 * * * *",
                "timezone": "Europe/London",
                "enabled": True,
                "misfire_policy": "run_once",
            }
        },
    }

    cron: str | None = Field(default=None, min_length=1, max_length=128, examples=["*/30 * * * *"])
    timezone: str | None = Field(default=None, min_length=1, max_length=64, examples=["Europe/London"])
    enabled: bool | None = Field(default=None, examples=[True])
    start_at: datetime | None = None
    end_at: datetime | None = None
    misfire_policy: Literal["skip", "run_once"] | None = Field(default=None, examples=["run_once"])


class ScheduleResponse(BaseModel):
    """Schedule representation with computed next_run_at and last_run_status."""

    id: UUID = Field(description="Schedule ID", examples=["55555555-5555-4555-8555-555555555555"])
    pipeline_id: UUID = Field(description="Associated Pipeline ID", examples=["11111111-1111-4111-8111-111111111111"])
    cron: str = Field(description="5-field cron string", examples=["0 * * * *"])
    timezone: str = Field(description="IANA timezone", examples=["UTC"])
    enabled: bool = Field(description="Schedule active status", examples=[True])
    start_at: datetime | None = Field(default=None, description="Optional schedule start boundary")
    end_at: datetime | None = Field(default=None, description="Optional schedule end boundary")
    misfire_policy: str = Field(description="Misfire handling policy (skip | run_once)", examples=["skip"])
    next_run_at: datetime | None = Field(default=None, description="Calculated next execution time in ISO-8601")
    last_run_status: str | None = Field(default=None, description="Status of most recent run triggered by schedule", examples=["succeeded"])
    created_at: datetime = Field(description="Created timestamp")
    updated_at: datetime = Field(description="Last update timestamp")

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": "55555555-5555-4555-8555-555555555555",
                "pipeline_id": "11111111-1111-4111-8111-111111111111",
                "cron": "0 * * * *",
                "timezone": "UTC",
                "enabled": True,
                "start_at": None,
                "end_at": None,
                "misfire_policy": "skip",
                "next_run_at": "2026-10-23T01:00:00Z",
                "last_run_status": "succeeded",
                "created_at": "2026-10-23T00:00:00Z",
                "updated_at": "2026-10-23T00:00:00Z",
            }
        },
    }


class SchedulePreviewRequest(BaseModel):
    """Preview next fire times request."""

    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "example": {
                "cron": "0 * * * *",
                "timezone": "UTC",
                "count": 5,
            }
        },
    }

    cron: str = Field(min_length=1, max_length=128, examples=["0 * * * *"])
    timezone: str = Field(min_length=1, max_length=64, examples=["UTC"])
    count: int = Field(default=5, ge=1, le=20, examples=[5])


class SchedulePreviewResponse(BaseModel):
    """Next fire times preview."""

    fire_times: list[datetime] = Field(description="List of predicted next trigger timestamps")

    model_config = {
        "json_schema_extra": {
            "example": {
                "fire_times": [
                    "2026-10-23T01:00:00Z",
                    "2026-10-23T02:00:00Z",
                    "2026-10-23T03:00:00Z",
                    "2026-10-23T04:00:00Z",
                    "2026-10-23T05:00:00Z",
                ]
            }
        }
    }
