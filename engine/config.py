"""Canonical pipeline configuration contract (Pydantic v2).

This module is the single source of truth for the node-and-edge JSON
contract required by ``ChaosFlow_Backend_Tasks.pdf``::

    {"pipeline": {"id": ..., "name": ..., "version": ..., "schedule": ...,
                  "nodes": [...], "edges": [...], "retry": {...}}}

NOTE on document conflict: the overall project guide describes a
hierarchical YAML configuration (source/schema/processing/storage). The
backend task specifies the JSON node-and-edge contract above. Per the
assignment rules, the JSON contract implemented here is canonical. The
YAML schema is NOT implemented; any future YAML support must go through
an explicit, documented contract change.

Validation rules enforced here (structural) + executor (semantic):
- unique node ids
- node categories: source | transform | sink
- edges reference existing nodes (no self-loops)
- graph is acyclic (checked in :mod:`engine.executor`)
- at least one source and one sink
- retry config valid (max_attempts >= 1, backoff_seconds >= 0)

Plugin-specific ``config`` validation is delegated to each plugin's own
Pydantic schema via the registry (see :mod:`engine.registry`).
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

NodeType = Literal["source", "transform", "sink"]


class NodeConfig(BaseModel):
    """A single pipeline node."""

    model_config = {"extra": "forbid"}

    id: str = Field(min_length=1, max_length=128)
    type: NodeType
    plugin: str = Field(min_length=1, max_length=64)
    config: dict[str, Any] = Field(default_factory=dict)


class EdgeConfig(BaseModel):
    """A directed edge between two nodes."""

    model_config = {"extra": "forbid", "populate_by_name": True}

    source: str = Field(min_length=1, validation_alias="from", serialization_alias="from")
    target: str = Field(min_length=1, validation_alias="to", serialization_alias="to")


class RetryConfig(BaseModel):
    """Retry policy. ``max_attempts`` INCLUDES the initial attempt."""

    model_config = {"extra": "forbid"}

    max_attempts: int = Field(default=3, ge=1, le=10)
    backoff_seconds: float = Field(default=5.0, ge=0.0, le=3600.0)


class PipelineConfig(BaseModel):
    """The inner ``pipeline`` object of the contract."""

    model_config = {"extra": "forbid"}

    id: UUID
    name: str = Field(min_length=1, max_length=256)
    version: int = Field(default=1, ge=1)
    schedule: str | None = None
    nodes: list[NodeConfig] = Field(min_length=1)
    edges: list[EdgeConfig] = Field(default_factory=list)
    retry: RetryConfig = Field(default_factory=RetryConfig)

    @field_validator("nodes")
    @classmethod
    def _unique_node_ids(cls, nodes: list[NodeConfig]) -> list[NodeConfig]:
        seen: set[str] = set()
        dupes: set[str] = set()
        for node in nodes:
            if node.id in seen:
                dupes.add(node.id)
            seen.add(node.id)
        if dupes:
            raise ValueError(f"Duplicate node ids: {sorted(dupes)}")
        return nodes

    @model_validator(mode="after")
    def _check_graph(self) -> PipelineConfig:
        ids = {node.id for node in self.nodes}
        for edge in self.edges:
            if edge.source not in ids:
                raise ValueError(f"Edge references unknown node: '{edge.source}'")
            if edge.target not in ids:
                raise ValueError(f"Edge references unknown node: '{edge.target}'")
            if edge.source == edge.target:
                raise ValueError(f"Self-loop edge on node '{edge.source}' is not allowed")
        kinds = {node.type for node in self.nodes}
        if "source" not in kinds:
            raise ValueError("Pipeline must contain at least one source node")
        if "sink" not in kinds:
            raise ValueError("Pipeline must contain at least one sink node")
        return nodes_acyclic_check(self)

    def node_map(self) -> dict[str, NodeConfig]:
        """Map node id -> node."""
        return {node.id: node for node in self.nodes}

    def predecessors(self, node_id: str) -> list[str]:
        """Return ids of nodes with edges into ``node_id``."""
        return [e.source for e in self.edges if e.target == node_id]

    def successors(self, node_id: str) -> list[str]:
        """Return ids of nodes with edges out of ``node_id``."""
        return [e.target for e in self.edges if e.source == node_id]


def nodes_acyclic_check(pipeline: PipelineConfig) -> PipelineConfig:
    """Raise ``ValueError`` if the graph contains a cycle (Kahn's algorithm)."""
    indegree: dict[str, int] = {node.id: 0 for node in pipeline.nodes}
    adjacency: dict[str, list[str]] = {node.id: [] for node in pipeline.nodes}
    for edge in pipeline.edges:
        adjacency[edge.source].append(edge.target)
        indegree[edge.target] += 1
    queue = sorted([nid for nid, deg in indegree.items() if deg == 0])
    visited = 0
    while queue:
        current = queue.pop(0)
        visited += 1
        for nxt in sorted(adjacency[current]):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    if visited != len(pipeline.nodes):
        raise ValueError("Pipeline graph must be acyclic (cycle detected)")
    return pipeline


class PipelineDocument(BaseModel):
    """Top-level document: ``{"pipeline": {...}}``."""

    model_config = {"extra": "forbid"}

    pipeline: PipelineConfig

    @classmethod
    def validate_json_document(cls, payload: dict[str, Any]) -> PipelineDocument:
        """Validate a raw JSON-decoded document, raising field-level errors."""
        return cls.model_validate(payload)
