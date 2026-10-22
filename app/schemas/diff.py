"""Diff and Rollback API models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class FieldDiff(BaseModel):
    """A single field difference between two versions."""

    path: str
    from_: Any = Field(default=None, alias="from")
    to: Any = Field(default=None, alias="to")


class NodeDiff(BaseModel):
    """Node-level diff."""

    id: str
    fields: list[FieldDiff] = Field(default_factory=list)


class DiffResponse(BaseModel):
    """Pipeline version diff: nodes and edges changes."""

    nodes: dict[str, list[NodeDiff]] = Field(default_factory=dict)
    edges: dict[str, list[tuple[str, str]]] = Field(default_factory=dict)

    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "example": {
                "nodes": {
                    "added": [{"id": "src_2", "fields": []}],
                    "removed": [{"id": "tr_old", "fields": []}],
                    "changed": [
                        {"id": "src_1", "fields": [{"path": "config.path", "from": "a.csv", "to": "b.csv"}]}
                    ],
                },
                "edges": {
                    "added": [["src_2", "tr_1"]],
                    "removed": [["src_1", "tr_old"]],
                },
            }
        },
    }


class RollbackResponse(BaseModel):
    """Rollback creates a new version (N+1) from an old version."""

    version: int = Field(examples=[3])
    config: dict[str, Any]
    created_at: datetime

    model_config = {
        "json_schema_extra": {
            "example": {
                "version": 3,
                "config": {
                    "pipeline": {
                        "id": "11111111-1111-4111-8111-111111111111",
                        "name": "orders-pipeline",
                        "version": 3,
                    }
                },
                "created_at": "2026-10-23T00:00:00Z",
            }
        }
    }
