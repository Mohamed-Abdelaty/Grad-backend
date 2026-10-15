"""Run and run-log API models."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class RunResponse(BaseModel):
    """Run status representation (live progress + counters)."""

    id: UUID
    pipeline_id: UUID
    version: int
    status: str
    progress: float
    records_in: int
    records_out: int
    error: str | None = None
    error_type: str | None = None
    failed_node_id: str | None = None
    metrics: dict[str, Any] | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    model_config = {"from_attributes": True, "populate_by_name": True}


class RunLogResponse(BaseModel):
    """Persisted run log/event row."""

    id: UUID
    timestamp: datetime
    level: str
    node_id: str | None = None
    event: str | None = None
    message: str
    records_in: int = 0
    records_out: int = 0

    model_config = {"from_attributes": True}


class PluginEntry(BaseModel):
    """One registered plugin with its configuration JSON Schema."""

    category: str
    name: str
    config_schema: dict[str, Any]
