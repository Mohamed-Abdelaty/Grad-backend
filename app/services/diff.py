"""Diff and Rollback service."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.models.entities import Pipeline, PipelineVersion
from app.schemas.diff import DiffResponse, FieldDiff, NodeDiff, RollbackResponse
from app.services.audit import AuditAction, AuditService


class DiffService:
    """Service for comparing pipeline versions."""

    @staticmethod
    def _flatten_config(config: dict[str, Any], prefix: str = "") -> dict[str, Any]:
        """Flatten a nested dict with dot-separated paths."""
        result = {}
        for key, value in config.items():
            full_key = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                result.update(DiffService._flatten_config(value, full_key))
            else:
                result[full_key] = value
        return result

    @staticmethod
    def diff_versions(
        db: Session,
        pipeline_id: UUID,
        from_version: int,
        to_version: int,
        user_email: str,
        request_ip: str | None = None,
    ) -> DiffResponse:
        """Compare two pipeline versions."""
        if from_version == to_version:
            return DiffResponse(nodes={}, edges={})

        pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
        if not pipeline:
            raise ApiError(404, "not_found", "Pipeline not found")

        from_ver = db.query(PipelineVersion).filter(
            PipelineVersion.pipeline_id == pipeline_id,
            PipelineVersion.version == from_version,
        ).first()
        to_ver = db.query(PipelineVersion).filter(
            PipelineVersion.pipeline_id == pipeline_id,
            PipelineVersion.version == to_version,
        ).first()

        if not from_ver or not to_ver:
            raise ApiError(404, "not_found", "One or both versions not found")

        from_nodes = {n["id"]: n for n in from_ver.config.get("pipeline", {}).get("nodes", [])}
        to_nodes = {n["id"]: n for n in to_ver.config.get("pipeline", {}).get("nodes", [])}
        from_edges = {(e["from"], e["to"]) for e in from_ver.config.get("pipeline", {}).get("edges", [])}
        to_edges = {(e["from"], e["to"]) for e in to_ver.config.get("pipeline", {}).get("edges", [])}

        # Nodes diff
        added_nodes = set(to_nodes.keys()) - set(from_nodes.keys())
        removed_nodes = set(from_nodes.keys()) - set(to_nodes.keys())
        common_nodes = set(from_nodes.keys()) & set(to_nodes.keys())

        # Edges diff
        added_edges = list(to_edges - from_edges)
        removed_edges = list(from_edges - to_edges)

        # Build changed_nodes as NodeDiff objects
        changed_nodes_objects: list[NodeDiff] = []
        for node_id in common_nodes:
            from_flat = DiffService._flatten_config(from_nodes[node_id])
            to_flat = DiffService._flatten_config(to_nodes[node_id])

            field_changes: list[FieldDiff] = []
            all_keys = set(from_flat.keys()) | set(to_flat.keys())
            for key in all_keys:
                from_val = from_flat.get(key)
                to_val = to_flat.get(key)
                if from_val != to_val:
                    field_changes.append(FieldDiff(path=key, **{"from": from_val}, to=to_val))

            if field_changes:
                changed_nodes_objects.append(NodeDiff(id=node_id, fields=field_changes))

        return DiffResponse(
            nodes={
                "added": [NodeDiff(id=n, fields=[]) for n in sorted(added_nodes)],
                "removed": [NodeDiff(id=n, fields=[]) for n in sorted(removed_nodes)],
                "changed": changed_nodes_objects,
            },
            edges={
                "added": [tuple(e) for e in sorted(added_edges)],
                "removed": [tuple(e) for e in sorted(removed_edges)],
            },
        )


class RollbackService:
    """Service for rolling back pipeline versions."""

    @staticmethod
    def rollback(
        db: Session,
        pipeline_id: UUID,
        target_version: int,
        user: Any,
        request_ip: str | None = None,
    ) -> RollbackResponse:
        """Rollback to a previous version by creating a new version (N+1)."""
        pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
        if not pipeline:
            raise ApiError(404, "not_found", "Pipeline not found")

        if pipeline.owner_id != user.id and user.role != "admin":
            raise ApiError(404, "not_found", "Pipeline not found")

        # Get target version
        target_ver = db.query(PipelineVersion).filter(
            PipelineVersion.pipeline_id == pipeline_id,
            PipelineVersion.version == target_version,
        ).first()
        if not target_ver:
            raise ApiError(404, "not_found", f"Version {target_version} not found")

        # Get current version
        current_version = pipeline.current_version
        new_version = current_version + 1

        # Copy config from target version (deepcopy prevents mutating original version)
        config = copy.deepcopy(target_ver.config)
        config["pipeline"]["id"] = str(pipeline_id)
        config["pipeline"]["name"] = pipeline.name
        config["pipeline"]["version"] = new_version

        # Validate the config against current plugin schemas
        from engine.errors import EngineError
        from engine.executor import validate_pipeline_dict
        try:
            validate_pipeline_dict(config)
        except EngineError as exc:
            raise ApiError(
                422, "invalid_pipeline_config",
                f"Rollback target version is no longer valid: {exc}"
            ) from exc

        # Create new version
        new_pipeline_version = PipelineVersion(
            pipeline_id=pipeline_id,
            version=new_version,
            config=config,
        )
        db.add(new_pipeline_version)

        # Update pipeline current version
        pipeline.current_version = new_version
        pipeline.updated_at = datetime.now(UTC)

        db.commit()
        db.refresh(pipeline)

        # Audit
        AuditService.record(
            db,
            actor=user.email,
            action=AuditAction.PIPELINE_ROLLBACK,
            target_type="pipeline",
            target_id=pipeline_id,
            ip=request_ip,
            metadata={"from_version": current_version, "to_version": target_version, "new_version": new_version},
        )

        return RollbackResponse(
            version=new_version,
            config=config["pipeline"],
            created_at=new_pipeline_version.created_at,
        )


def validate_rollback_config(pipeline_id: UUID, target_version: int, db: Session) -> tuple[bool, str | None]:
    """Validate that a rollback target version is still compatible with current plugins."""
    target_ver = db.query(PipelineVersion).filter(
        PipelineVersion.pipeline_id == pipeline_id,
        PipelineVersion.version == target_version,
    ).first()
    if not target_ver:
        return False, "Version not found"

    from engine.errors import EngineError
    from engine.executor import validate_pipeline_dict
    try:
        config = target_ver.config.copy()
        config["pipeline"]["version"] = pipeline_id  # Just for validation
        validate_pipeline_dict(config)
        return True, None
    except EngineError as exc:
        return False, str(exc)
