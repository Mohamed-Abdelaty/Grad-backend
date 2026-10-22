"""Pipeline and version API models."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class PipelineCreate(BaseModel):
    """Create-pipeline payload; ``config`` must be a valid pipeline document."""

    name: str = Field(min_length=1, max_length=256, examples=["orders-pipeline"], description="Pipeline name")
    config: dict[str, Any] = Field(
        description="Full pipeline configuration document conforming to engine schema",
        examples=[
            {
                "pipeline": {
                    "id": "11111111-1111-4111-8111-111111111111",
                    "name": "orders-pipeline",
                    "version": 1,
                    "schedule": None,
                    "nodes": [
                        {
                            "id": "src_1",
                            "type": "source",
                            "plugin": "csv",
                            "config": {"path": "samples/data/orders.csv", "delimiter": ","},
                        },
                        {
                            "id": "tr_1",
                            "type": "transform",
                            "plugin": "filter",
                            "config": {"expression": "amount > 100"},
                        },
                        {
                            "id": "snk_1",
                            "type": "sink",
                            "plugin": "csv",
                            "config": {"path": "outputs/orders_clean.csv", "mode": "overwrite"},
                        },
                    ],
                    "edges": [
                        {"from": "src_1", "to": "tr_1"},
                        {"from": "tr_1", "to": "snk_1"},
                    ],
                    "retry": {"max_attempts": 3, "backoff_seconds": 5},
                }
            }
        ],
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "orders-pipeline",
                "config": {
                    "pipeline": {
                        "id": "11111111-1111-4111-8111-111111111111",
                        "name": "orders-pipeline",
                        "version": 1,
                        "schedule": None,
                        "nodes": [
                            {
                                "id": "src_1",
                                "type": "source",
                                "plugin": "csv",
                                "config": {"path": "samples/data/orders.csv", "delimiter": ","},
                            },
                            {
                                "id": "tr_1",
                                "type": "transform",
                                "plugin": "filter",
                                "config": {"expression": "amount > 100"},
                            },
                            {
                                "id": "snk_1",
                                "type": "sink",
                                "plugin": "csv",
                                "config": {"path": "outputs/orders_clean.csv", "mode": "overwrite"},
                            },
                        ],
                        "edges": [
                            {"from": "src_1", "to": "tr_1"},
                            {"from": "tr_1", "to": "snk_1"},
                        ],
                        "retry": {"max_attempts": 3, "backoff_seconds": 5},
                    }
                },
            }
        }
    }


class PipelineUpdate(BaseModel):
    """Update-pipeline payload; a new immutable version is created."""

    name: str | None = Field(default=None, min_length=1, max_length=256, examples=["orders-pipeline-v2"])
    config: dict[str, Any] | None = Field(
        default=None,
        description="Updated pipeline configuration document",
        examples=[
            {
                "pipeline": {
                    "id": "11111111-1111-4111-8111-111111111111",
                    "name": "orders-pipeline-v2",
                    "version": 2,
                    "schedule": None,
                    "nodes": [
                        {
                            "id": "src_1",
                            "type": "source",
                            "plugin": "csv",
                            "config": {"path": "samples/data/orders.csv", "delimiter": ","},
                        },
                        {
                            "id": "tr_1",
                            "type": "transform",
                            "plugin": "filter",
                            "config": {"expression": "amount > 200"},
                        },
                        {
                            "id": "snk_1",
                            "type": "sink",
                            "plugin": "csv",
                            "config": {"path": "outputs/orders_clean.csv", "mode": "overwrite"},
                        },
                    ],
                    "edges": [
                        {"from": "src_1", "to": "tr_1"},
                        {"from": "tr_1", "to": "snk_1"},
                    ],
                    "retry": {"max_attempts": 3, "backoff_seconds": 5},
                }
            }
        ],
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "orders-pipeline-v2",
                "config": {
                    "pipeline": {
                        "id": "11111111-1111-4111-8111-111111111111",
                        "name": "orders-pipeline-v2",
                        "version": 2,
                        "schedule": None,
                        "nodes": [
                            {
                                "id": "src_1",
                                "type": "source",
                                "plugin": "csv",
                                "config": {"path": "samples/data/orders.csv", "delimiter": ","},
                            },
                            {
                                "id": "tr_1",
                                "type": "transform",
                                "plugin": "filter",
                                "config": {"expression": "amount > 200"},
                            },
                            {
                                "id": "snk_1",
                                "type": "sink",
                                "plugin": "csv",
                                "config": {"path": "outputs/orders_clean.csv", "mode": "overwrite"},
                            },
                        ],
                        "edges": [
                            {"from": "src_1", "to": "tr_1"},
                            {"from": "tr_1", "to": "snk_1"},
                        ],
                        "retry": {"max_attempts": 3, "backoff_seconds": 5},
                    }
                },
            }
        }
    }


class PipelineResponse(BaseModel):
    """Pipeline list/detail representation."""

    id: UUID = Field(description="Unique pipeline ID", examples=["11111111-1111-4111-8111-111111111111"])
    name: str = Field(description="Pipeline display name", examples=["orders-pipeline"])
    owner_id: UUID = Field(description="User ID of owner", examples=["22222222-2222-4222-8222-222222222222"])
    current_version: int = Field(description="Latest version number", examples=[1])
    created_at: datetime = Field(description="Creation timestamp")
    updated_at: datetime = Field(description="Last update timestamp")

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": "11111111-1111-4111-8111-111111111111",
                "name": "orders-pipeline",
                "owner_id": "22222222-2222-4222-8222-222222222222",
                "current_version": 1,
                "created_at": "2026-10-23T00:00:00Z",
                "updated_at": "2026-10-23T00:00:00Z",
            }
        },
    }


class PipelineDetailResponse(PipelineResponse):
    """Pipeline detail including the current configuration snapshot."""

    config: dict[str, Any] = Field(description="Current pipeline configuration document")

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": "11111111-1111-4111-8111-111111111111",
                "name": "orders-pipeline",
                "owner_id": "22222222-2222-4222-8222-222222222222",
                "current_version": 1,
                "created_at": "2026-10-23T00:00:00Z",
                "updated_at": "2026-10-23T00:00:00Z",
                "config": {
                    "pipeline": {
                        "id": "11111111-1111-4111-8111-111111111111",
                        "name": "orders-pipeline",
                        "version": 1,
                        "schedule": None,
                        "nodes": [
                            {
                                "id": "src_1",
                                "type": "source",
                                "plugin": "csv",
                                "config": {"path": "samples/data/orders.csv", "delimiter": ","},
                            },
                            {
                                "id": "tr_1",
                                "type": "transform",
                                "plugin": "filter",
                                "config": {"expression": "amount > 100"},
                            },
                            {
                                "id": "snk_1",
                                "type": "sink",
                                "plugin": "csv",
                                "config": {"path": "outputs/orders_clean.csv", "mode": "overwrite"},
                            },
                        ],
                        "edges": [
                            {"from": "src_1", "to": "tr_1"},
                            {"from": "tr_1", "to": "snk_1"},
                        ],
                        "retry": {"max_attempts": 3, "backoff_seconds": 5},
                    }
                },
            }
        },
    }


class PipelineVersionResponse(BaseModel):
    """One immutable configuration version."""

    version: int = Field(description="Version index", examples=[1])
    config: dict[str, Any] = Field(description="Snapshot of pipeline config at this version")
    created_at: datetime = Field(description="Version commit timestamp")

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "version": 1,
                "config": {
                    "pipeline": {
                        "id": "11111111-1111-4111-8111-111111111111",
                        "name": "orders-pipeline",
                        "version": 1,
                    }
                },
                "created_at": "2026-10-23T00:00:00Z",
            }
        },
    }

    model_config = {"from_attributes": True}
