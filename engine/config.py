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

from collections import deque
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
        errors: list[str] = []
        node_types = {node.id: node.type for node in self.nodes}
        incoming_counts: dict[str, int] = dict.fromkeys(ids, 0)
        outgoing_counts: dict[str, int] = dict.fromkeys(ids, 0)

        for edge in self.edges:
            if edge.source not in ids:
                errors.append(f"Edge references unknown node: '{edge.source}'")
            if edge.target not in ids:
                errors.append(f"Edge references unknown node: '{edge.target}'")
            if edge.source in ids and edge.target in ids:
                if edge.source == edge.target:
                    errors.append(f"Self-loop edge on node '{edge.source}' is not allowed")
                else:
                    outgoing_counts[edge.source] = outgoing_counts.get(edge.source, 0) + 1
                    incoming_counts[edge.target] = incoming_counts.get(edge.target, 0) + 1
                    if node_types.get(edge.target) == "source":
                        errors.append(
                            f"Source node '{edge.target}' cannot have incoming edge from '{edge.source}'"
                        )
                    if node_types.get(edge.source) == "sink":
                        errors.append(
                            f"Sink node '{edge.source}' cannot have outgoing edge to '{edge.target}'"
                        )

        kinds = {node.type for node in self.nodes}
        if "source" not in kinds:
            errors.append("Pipeline must contain at least one source node")
        if "sink" not in kinds:
            errors.append("Pipeline must contain at least one sink node")

        # Check orphan nodes (disconnected nodes when multiple nodes exist and edges exist)
        if len(self.nodes) > 1 and len(self.edges) > 0:
            for node in self.nodes:
                if incoming_counts.get(node.id, 0) == 0 and outgoing_counts.get(node.id, 0) == 0:
                    errors.append(f"Orphan node '{node.id}' is not connected to any other node")

        # Kahn's algorithm for cycles
        try:
            nodes_acyclic_check(self)
        except ValueError as exc:
            errors.append(str(exc))

        if errors:
            raise ValueError(f"Graph validation failed: {'; '.join(errors)}")
        return self

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
    queue = deque(sorted([nid for nid, deg in indegree.items() if deg == 0]))
    visited = 0
    while queue:
        current = queue.popleft()
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


def collect_all_pipeline_errors(payload: dict[str, Any]) -> list[str]:
    """Collect all validation errors across structure, DAG graph, and plugin configs."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["Document must be a JSON object"]
    pipeline = payload.get("pipeline")
    if not isinstance(pipeline, dict):
        return ["Top-level document must contain a 'pipeline' object"]

    # 1. Pipeline ID
    pid = pipeline.get("id")
    if not pid:
        errors.append("Pipeline id is required")
    else:
        try:
            UUID(str(pid))
        except (ValueError, AttributeError):
            errors.append(f"Pipeline id must be a valid UUID: '{pid}'")

    # 2. Pipeline Name
    name = pipeline.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        errors.append("Pipeline name must be a non-empty string")

    # 3. Nodes
    raw_nodes = pipeline.get("nodes")
    if not isinstance(raw_nodes, list) or len(raw_nodes) == 0:
        errors.append("Pipeline must contain at least one node")
        raw_nodes = []

    node_ids: list[str] = []
    seen_ids: set[str] = set()
    dup_ids: set[str] = set()
    node_types: dict[str, str] = {}
    valid_node_objects: dict[str, dict[str, Any]] = {}

    for idx, n in enumerate(raw_nodes):
        if not isinstance(n, dict):
            errors.append(f"Node at index {idx} must be an object")
            continue
        nid = n.get("id")
        ntype = n.get("type")

        if not nid or not isinstance(nid, str):
            errors.append(f"Node at index {idx} requires a non-empty 'id'")
            continue

        if nid in seen_ids:
            dup_ids.add(nid)
        seen_ids.add(nid)
        node_ids.append(nid)

        if ntype not in ("source", "transform", "sink"):
            errors.append(f"Node '{nid}': invalid type '{ntype}' (must be source, transform, or sink)")
        else:
            node_types[nid] = ntype

        valid_node_objects[nid] = n

    if dup_ids:
        errors.append(f"Duplicate node ids: {sorted(dup_ids)}")

    unique_ids = set(node_ids)

    # Check source and sink presence
    types_set = set(node_types.values())
    if "source" not in types_set:
        errors.append("Pipeline must contain at least one source node")
    if "sink" not in types_set:
        errors.append("Pipeline must contain at least one sink node")

    # 4. Edges
    raw_edges = pipeline.get("edges", [])
    if not isinstance(raw_edges, list):
        errors.append("Pipeline edges must be a list")
        raw_edges = []

    valid_edges: list[tuple[str, str]] = []
    incoming_counts: dict[str, int] = dict.fromkeys(unique_ids, 0)
    outgoing_counts: dict[str, int] = dict.fromkeys(unique_ids, 0)

    for idx, e in enumerate(raw_edges):
        if not isinstance(e, dict):
            errors.append(f"Edge at index {idx} must be an object")
            continue
        src = e.get("from") or e.get("source")
        tgt = e.get("to") or e.get("target")

        if not src or src not in unique_ids:
            errors.append(f"Edge references unknown node: '{src}'")
        if not tgt or tgt not in unique_ids:
            errors.append(f"Edge references unknown node: '{tgt}'")

        if src in unique_ids and tgt in unique_ids:
            if src == tgt:
                errors.append(f"Self-loop edge on node '{src}' is not allowed")
            else:
                valid_edges.append((src, tgt))
                outgoing_counts[src] = outgoing_counts.get(src, 0) + 1
                incoming_counts[tgt] = incoming_counts.get(tgt, 0) + 1

                # Check wrong position
                if node_types.get(tgt) == "source":
                    errors.append(f"Source node '{tgt}' cannot have incoming edge from '{src}'")
                if node_types.get(src) == "sink":
                    errors.append(f"Sink node '{src}' cannot have outgoing edge to '{tgt}'")

    # Check orphan nodes (degree 0 when edges exist and len(unique_ids) > 1)
    if len(unique_ids) > 1 and len(raw_edges) > 0:
        for nid in sorted(unique_ids):
            if incoming_counts.get(nid, 0) == 0 and outgoing_counts.get(nid, 0) == 0:
                errors.append(f"Orphan node '{nid}' is not connected to any other node")

    # Check cycles (Kahn's algorithm on valid edges)
    if valid_edges and unique_ids:
        indegree = dict.fromkeys(unique_ids, 0)
        adj: dict[str, list[str]] = {nid: [] for nid in unique_ids}
        for s, t in valid_edges:
            adj[s].append(t)
            indegree[t] += 1
        queue = deque(sorted([nid for nid, deg in indegree.items() if deg == 0]))
        visited = 0
        while queue:
            curr = queue.popleft()
            visited += 1
            for nxt in adj[curr]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    queue.append(nxt)
        if visited != len(unique_ids):
            errors.append("Pipeline graph must be acyclic (cycle detected)")

    # 5. Retry config
    retry = pipeline.get("retry")
    if retry is not None and isinstance(retry, dict):
        max_attempts = retry.get("max_attempts", 3)
        backoff_seconds = retry.get("backoff_seconds", 5.0)
        if not isinstance(max_attempts, int) or max_attempts < 1:
            errors.append(f"Retry max_attempts must be an integer >= 1: {max_attempts}")
        if not isinstance(backoff_seconds, (int, float)) or backoff_seconds < 0:
            errors.append(f"Retry backoff_seconds must be a number >= 0: {backoff_seconds}")

    # 6. Plugin existence and per-plugin schemas
    try:
        from engine.errors import PluginConfigError, UnknownPluginError
        from engine.registry import lookup

        for nid, node_data in valid_node_objects.items():
            ntype = node_data.get("type")
            nplugin = node_data.get("plugin")
            nconfig = node_data.get("config", {})
            if ntype not in ("source", "transform", "sink") or not nplugin:
                continue
            try:
                plugin_cls = lookup(ntype, str(nplugin))
            except UnknownPluginError:
                errors.append(f"Node '{nid}': Unknown {ntype} plugin: '{nplugin}'")
                continue
            except Exception as exc:
                errors.append(f"Node '{nid}': Failed resolving plugin '{nplugin}': {exc}")
                continue

            try:
                plugin = plugin_cls(nconfig)
                plugin.validate_config()
            except PluginConfigError as exc:
                msg = str(exc).split(": ", 1)[-1] if ": " in str(exc) else str(exc)
                errors.append(f"Node '{nid}': {msg}")
            except Exception as exc:
                errors.append(f"Node '{nid}': {exc}")
    except Exception:
        pass

    return errors

