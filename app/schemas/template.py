"""Template API models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class TemplateCategory(str):
    ETL = "ETL"
    ANALYTICS = "Analytics"
    SYNC = "Sync"


class TemplateVisibility(str):
    PRIVATE = "private"
    PUBLIC = "public"


class TemplateCreate(BaseModel):
    """Create-template payload (user-created private template)."""

    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "example": {
                "name": "My Custom ETL",
                "category": "ETL",
                "description": "Custom CSV filter and extract template",
                "config": {
                    "pipeline": {
                        "name": "Custom ETL",
                        "nodes": [
                            {"id": "src_1", "type": "source", "plugin": "csv", "config": {"path": "samples/data/orders.csv"}},
                            {"id": "tr_1", "type": "transform", "plugin": "filter", "config": {"expression": "amount > 50"}},
                            {"id": "snk_1", "type": "sink", "plugin": "csv", "config": {"path": "outputs/clean.csv"}},
                        ],
                        "edges": [{"from": "src_1", "to": "tr_1"}, {"from": "tr_1", "to": "snk_1"}],
                    }
                },
            }
        },
    }

    name: str = Field(min_length=1, max_length=256, examples=["My Custom ETL"])
    category: Literal["ETL", "Analytics", "Sync"] = Field(examples=["ETL"])
    description: str = Field(min_length=1, max_length=1024, examples=["Custom CSV filter and extract template"])
    config: dict[str, Any] = Field(description="Pipeline configuration blueprint")


class TemplateUpdate(BaseModel):
    """Update-template payload (only owner can update private templates)."""

    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "example": {
                "name": "My Custom ETL v2",
                "category": "Analytics",
                "description": "Updated template description",
            }
        },
    }

    name: str | None = Field(default=None, min_length=1, max_length=256, examples=["My Custom ETL v2"])
    category: Literal["ETL", "Analytics", "Sync"] | None = Field(default=None, examples=["Analytics"])
    description: str | None = Field(default=None, min_length=1, max_length=1024, examples=["Updated template description"])
    config: dict[str, Any] | None = None
    visibility: Literal["private", "public"] | None = None


class TemplateResponse(BaseModel):
    """Template representation."""

    id: UUID = Field(examples=["66666666-6666-4666-8666-666666666666"])
    name: str = Field(examples=["CSV to SQLite ETL"])
    category: str = Field(examples=["ETL"])
    description: str = Field(examples=["Extract transactions and load to SQLite database"])
    config: dict[str, Any]
    usage_count: int = Field(examples=[0])
    owner_id: UUID | None = Field(default=None, examples=["22222222-2222-4222-8222-222222222222"])
    visibility: Literal["private", "public"] = Field(examples=["public"])
    is_published: bool = Field(examples=[True])
    created_at: datetime
    updated_at: datetime

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": "66666666-6666-4666-8666-666666666666",
                "name": "CSV to SQLite ETL",
                "category": "ETL",
                "description": "Extract transactions and load to SQLite database",
                "config": {"pipeline": {"name": "CSV to SQLite ETL"}},
                "usage_count": 5,
                "owner_id": None,
                "visibility": "public",
                "is_published": True,
                "created_at": "2026-10-23T00:00:00Z",
                "updated_at": "2026-10-23T00:00:00Z",
            }
        },
    }


class TemplateListResponse(BaseModel):
    """Paginated templates list."""

    items: list[TemplateResponse]
    total: int = Field(examples=[6])
    page: int = Field(examples=[1])
    page_size: int = Field(examples=[20])

    model_config = {
        "json_schema_extra": {
            "example": {
                "items": [],
                "total": 6,
                "page": 1,
                "page_size": 20,
            }
        }
    }


class TemplateUseResponse(BaseModel):
    """Response when using a template to create a pipeline."""

    pipeline_id: UUID = Field(examples=["11111111-1111-4111-8111-111111111111"])
    name: str = Field(examples=["My Cloned Pipeline"])
    config: dict[str, Any]
    version: int = Field(default=1, examples=[1])

    model_config = {
        "json_schema_extra": {
            "example": {
                "pipeline_id": "11111111-1111-4111-8111-111111111111",
                "name": "My Cloned Pipeline",
                "config": {"pipeline": {"name": "My Cloned Pipeline", "version": 1}},
                "version": 1,
            }
        }
    }
