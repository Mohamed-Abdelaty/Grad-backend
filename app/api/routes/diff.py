"""Diff and Rollback endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import require_scope
from app.core.errors import ApiError
from app.db.session import get_db
from app.models.entities import Pipeline, PipelineVersion, User
from app.schemas.diff import DiffResponse, RollbackResponse
from app.services.apikey import Principal
from app.services.diff import DiffService, RollbackService

router = APIRouter(prefix="/pipelines", tags=["diff-rollback"])


def _check_pipeline_access(pipeline: Pipeline, user: User) -> None:
    if user.role != "admin" and pipeline.owner_id != user.id:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Pipeline not found")


@router.get("/{pipeline_id}/versions/diff", response_model=DiffResponse)
def diff_versions(
    pipeline_id: UUID,
    from_version: int = Query(..., ge=1, alias="from"),
    to_version: int = Query(..., ge=1, alias="to"),
    principal: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> DiffResponse:
    """Compare two pipeline versions (nodes and edges)."""
    user = principal.user
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if pipeline is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Pipeline not found")
    _check_pipeline_access(pipeline, user)

    return DiffService.diff_versions(db, pipeline_id, from_version, to_version, user.email)


@router.post("/{pipeline_id}/rollback/{target_version}", response_model=RollbackResponse, status_code=status.HTTP_201_CREATED)
def rollback_pipeline(
    pipeline_id: UUID,
    target_version: int,
    request: Request,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> RollbackResponse:
    """Rollback to a previous version by creating a new version (N+1)."""
    user = principal.user
    pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
    if pipeline is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Pipeline not found")
    _check_pipeline_access(pipeline, user)

    # Validate target version exists
    target_ver = db.query(PipelineVersion).filter(
        PipelineVersion.pipeline_id == pipeline_id,
        PipelineVersion.version == target_version,
    ).first()
    if not target_ver:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", f"Version {target_version} not found")

    # Cannot rollback to current version
    if target_version == pipeline.current_version:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "already_current",
            f"Pipeline is already at version {target_version}"
        )

    return RollbackService.rollback(
        db, pipeline_id, target_version, user, request.client.host if request.client else None
    )
