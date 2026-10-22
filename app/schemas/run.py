"""Run and run-log API models."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class RunResponse(BaseModel):
    """Run status representation (live progress + counters)."""

    id: UUID = Field(description="Run ID", examples=["33333333-3333-4333-8333-333333333333"])
    pipeline_id: UUID = Field(description="Pipeline ID executed", examples=["11111111-1111-4111-8111-111111111111"])
    version: int = Field(description="Pipeline configuration version executed", examples=[1])
    status: str = Field(description="Run status: pending, running, succeeded, failed, cancelled", examples=["succeeded"])
    progress: float = Field(description="Execution progress percentage (0.0 to 1.0)", examples=[1.0])
    records_in: int = Field(description="Total input records processed", examples=[100])
    records_out: int = Field(description="Total output records emitted", examples=[95])
    error: str | None = Field(default=None, description="Error message if run failed", examples=[None])
    error_type: str | None = Field(default=None, description="Error classification category", examples=[None])
    failed_node_id: str | None = Field(default=None, description="ID of node where failure occurred", examples=[None])
    metrics: dict[str, Any] | None = Field(default=None, description="Runtime execution metrics and timings", examples=[{"duration_ms": 1250}])
    created_at: datetime = Field(description="Queued timestamp")
    started_at: datetime | None = Field(default=None, description="Start timestamp")
    finished_at: datetime | None = Field(default=None, description="Completion timestamp")

    model_config = {
        "from_attributes": True,
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "id": "33333333-3333-4333-8333-333333333333",
                "pipeline_id": "11111111-1111-4111-8111-111111111111",
                "version": 1,
                "status": "succeeded",
                "progress": 1.0,
                "records_in": 100,
                "records_out": 95,
                "error": None,
                "error_type": None,
                "failed_node_id": None,
                "metrics": {"duration_ms": 1250},
                "created_at": "2026-10-23T00:00:00Z",
                "started_at": "2026-10-23T00:00:01Z",
                "finished_at": "2026-10-23T00:00:02Z",
            }
        },
    }


class RunLogResponse(BaseModel):
    """Persisted run log/event row."""

    id: UUID = Field(description="Log entry ID", examples=["44444444-4444-4444-8444-444444444444"])
    timestamp: datetime = Field(description="Timestamp of event log")
    level: str = Field(description="Log severity level: info, warning, error", examples=["info"])
    node_id: str | None = Field(default=None, description="Node ID associated with log", examples=["tr_1"])
    event: str | None = Field(default=None, description="Structured event name", examples=["node_finish"])
    message: str = Field(description="Human readable log text", examples=["Node tr_1 transformed 95 records."])
    records_in: int = Field(default=0, description="Records read at this step", examples=[100])
    records_out: int = Field(default=0, description="Records written at this step", examples=[95])

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": "44444444-4444-4444-8444-444444444444",
                "timestamp": "2026-10-23T00:00:01Z",
                "level": "info",
                "node_id": "tr_1",
                "event": "node_finish",
                "message": "Node tr_1 transformed 95 records.",
                "records_in": 100,
                "records_out": 95,
            }
        },
    }


class PluginEntry(BaseModel):
    """One registered plugin with its configuration JSON Schema."""

    category: str = Field(description="Plugin category: source, transform, or sink", examples=["source"])
    name: str = Field(description="Plugin identifier name", examples=["csv"])
    config_schema: dict[str, Any] = Field(description="JSON schema describing accepted node configuration")

    model_config = {
        "json_schema_extra": {
            "example": {
                "category": "source",
                "name": "csv",
                "config_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "delimiter": {"type": "string", "default": ","},
                    },
                    "required": ["path"],
                },
            }
        }
    }
