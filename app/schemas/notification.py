"""Notification API models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class NotificationType(str):
    RUN_FAILED = "run_failed"
    RUN_SUCCEEDED = "run_succeeded"
    SCHEDULE_MISSED = "schedule_missed"


class ChannelType(str):
    IN_APP = "in_app"
    EMAIL = "email"
    WEBHOOK = "webhook"


class NotificationResponse(BaseModel):
    """In-app notification representation."""

    id: UUID = Field(examples=["77777777-7777-4777-8777-777777777777"])
    type: Literal["run_failed", "run_succeeded", "schedule_missed"] = Field(examples=["run_succeeded"])
    title: str = Field(examples=["Pipeline run succeeded"])
    body: str = Field(examples=["Pipeline 'orders-pipeline' run completed successfully."])
    run_id: UUID | None = Field(default=None, examples=["33333333-3333-4333-8333-333333333333"])
    read: bool = Field(examples=[False])
    created_at: datetime

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": "77777777-7777-4777-8777-777777777777",
                "type": "run_succeeded",
                "title": "Pipeline run succeeded",
                "body": "Pipeline 'orders-pipeline' run completed successfully.",
                "run_id": "33333333-3333-4333-8333-333333333333",
                "read": False,
                "created_at": "2026-10-23T00:00:00Z",
            }
        },
    }


class NotificationListResponse(BaseModel):
    """Paginated notifications list."""

    items: list[NotificationResponse]
    total: int = Field(examples=[1])
    page: int = Field(examples=[1])
    page_size: int = Field(examples=[20])
    unread_count: int = Field(examples=[1])

    model_config = {
        "json_schema_extra": {
            "example": {
                "items": [
                    {
                        "id": "77777777-7777-4777-8777-777777777777",
                        "type": "run_succeeded",
                        "title": "Pipeline run succeeded",
                        "body": "Pipeline 'orders-pipeline' run completed successfully.",
                        "run_id": "33333333-3333-4333-8333-333333333333",
                        "read": False,
                        "created_at": "2026-10-23T00:00:00Z",
                    }
                ],
                "total": 1,
                "page": 1,
                "page_size": 20,
                "unread_count": 1,
            }
        }
    }


class NotificationReadRequest(BaseModel):
    """Mark notification as read."""

    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "example": {
                "read": True
            }
        },
    }

    read: bool = Field(default=True, examples=[True])


class NotificationPreferencesResponse(BaseModel):
    """User notification preferences per event type and channel."""

    model_config = {
        "extra": "forbid",
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "run_failed": {"in_app": True, "email": True, "webhook": True},
                "run_succeeded": {"in_app": True, "email": False, "webhook": False},
                "schedule_missed": {"in_app": True, "email": False, "webhook": True},
            }
        },
    }

    run_failed: dict[str, bool] = Field(
        default_factory=lambda: {"in_app": True, "email": False, "webhook": False}
    )
    run_succeeded: dict[str, bool] = Field(
        default_factory=lambda: {"in_app": True, "email": False, "webhook": False}
    )
    schedule_missed: dict[str, bool] = Field(
        default_factory=lambda: {"in_app": True, "email": False, "webhook": False}
    )


class NotificationPreferencesUpdate(BaseModel):
    """Update notification preferences."""

    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "example": {
                "run_failed": {"in_app": True, "email": True, "webhook": True},
                "run_succeeded": {"in_app": True, "email": False, "webhook": False},
                "schedule_missed": {"in_app": True, "email": False, "webhook": True},
            }
        },
    }

    run_failed: dict[str, bool] | None = None
    run_succeeded: dict[str, bool] | None = None
    schedule_missed: dict[str, bool] | None = None


class WebhookConfig(BaseModel):
    """Webhook delivery configuration."""

    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "example": {
                "url": "http://localhost:9000/webhook",
                "secret": "top-secret-webhook-key-123456",
            }
        },
    }

    url: str = Field(min_length=1, max_length=512, examples=["http://localhost:9000/webhook"])
    secret: str = Field(min_length=16, max_length=128, examples=["top-secret-webhook-key-123456"])


class WebhookConfigResponse(BaseModel):
    """Webhook config without secret (write-only after creation)."""

    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "example": {
                "url": "http://localhost:9000/webhook",
                "secret": "",
            }
        },
    }

    url: str = Field(examples=["http://localhost:9000/webhook"])
    secret: str = Field(default="", description="Write-only; not returned after creation")


class EmailConfig(BaseModel):
    """Email delivery configuration."""

    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "example": {
                "email": "alerts@example.com"
            }
        },
    }

    email: str = Field(min_length=1, max_length=320, examples=["alerts@example.com"])


class DeliveryLogResponse(BaseModel):
    """Notification delivery attempt log."""

    id: UUID = Field(examples=["88888888-8888-4888-8888-888888888888"])
    notification_id: UUID = Field(examples=["77777777-7777-4777-8777-777777777777"])
    channel: Literal["in_app", "email", "webhook"] = Field(examples=["webhook"])
    target: str = Field(examples=["http://localhost:9000/webhook"])
    status: Literal["pending", "sent", "failed"] = Field(examples=["sent"])
    attempts: int = Field(examples=[1])
    last_error: str | None = Field(default=None, examples=[None])
    created_at: datetime
    last_attempt_at: datetime | None = None

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": "88888888-8888-4888-8888-888888888888",
                "notification_id": "77777777-7777-4777-8777-777777777777",
                "channel": "webhook",
                "target": "http://localhost:9000/webhook",
                "status": "sent",
                "attempts": 1,
                "last_error": None,
                "created_at": "2026-10-23T00:00:00Z",
                "last_attempt_at": "2026-10-23T00:00:01Z",
            }
        },
    }
