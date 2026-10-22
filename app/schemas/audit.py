"""Audit log API models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class AuditAction(str):
    AUTH_LOGIN_SUCCESS = "auth.login_success"
    AUTH_LOGIN_FAILED = "auth.login_failed"
    PIPELINE_CREATE = "pipeline.create"
    PIPELINE_UPDATE = "pipeline.update"
    PIPELINE_DELETE = "pipeline.delete"
    PIPELINE_ROLLBACK = "pipeline.rollback"
    RUN_START = "run.start"
    RUN_STOP = "run.stop"
    APIKEY_CREATE = "apikey.create"
    APIKEY_REVOKE = "apikey.revoke"
    USER_ROLE_CHANGE = "user.role_change"
    SCHEDULE_CREATE = "schedule.create"
    SCHEDULE_UPDATE = "schedule.update"
    SCHEDULE_DELETE = "schedule.delete"
    TEMPLATE_PUBLISH = "template.publish"


class AuditEventResponse(BaseModel):
    """Audit log event representation."""

    id: UUID = Field(examples=["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"])
    actor: str = Field(examples=["admin@test.com"])
    action: str = Field(examples=["pipeline.create"])
    target_type: str | None = Field(default=None, examples=["pipeline"])
    target_id: UUID | None = Field(default=None, examples=["11111111-1111-4111-8111-111111111111"])
    ip: str | None = Field(default=None, examples=["127.0.0.1"])
    created_at: datetime

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "actor": "admin@test.com",
                "action": "pipeline.create",
                "target_type": "pipeline",
                "target_id": "11111111-1111-4111-8111-111111111111",
                "ip": "127.0.0.1",
                "created_at": "2026-10-23T00:00:00Z",
            }
        },
    }


class AuditLogListResponse(BaseModel):
    """Paginated audit log list."""

    items: list[AuditEventResponse]
    total: int = Field(examples=[1])
    page: int = Field(examples=[1])
    page_size: int = Field(examples=[20])

    model_config = {
        "json_schema_extra": {
            "example": {
                "items": [
                    {
                        "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                        "actor": "admin@test.com",
                        "action": "pipeline.create",
                        "target_type": "pipeline",
                        "target_id": "11111111-1111-4111-8111-111111111111",
                        "ip": "127.0.0.1",
                        "created_at": "2026-10-23T00:00:00Z",
                    }
                ],
                "total": 1,
                "page": 1,
                "page_size": 20,
            }
        }
    }


class AuditLogFilters(BaseModel):
    """Audit log filter parameters."""

    model_config = {
        "extra": "forbid",
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "actor": "admin@test.com",
                "action": "pipeline.create",
                "page": 1,
                "page_size": 20,
                "format": "json",
            }
        },
    }

    actor: str | None = Field(default=None, examples=["admin@test.com"])
    action: str | None = Field(default=None, examples=["pipeline.create"])
    target_type: str | None = Field(default=None, examples=["pipeline"])
    target_id: UUID | None = None
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None
    page: int = Field(default=1, ge=1, examples=[1])
    page_size: int = Field(default=20, ge=1, le=100, examples=[20])
    format: Literal["json", "csv"] = Field(default="json", examples=["json"])
