"""Pipeline CRUD with config validation and immutable version history."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import require_scope
from app.core.errors import ApiError
from app.db.session import get_db
from app.models.entities import Pipeline, PipelineVersion, User
from app.schemas.common import Page
from app.schemas.pipeline import (
    PipelineCreate,
    PipelineDetailResponse,
    PipelineResponse,
    PipelineUpdate,
    PipelineVersionResponse,
)
from app.services.apikey import Principal
from app.services.audit import AuditAction, AuditService
from engine.errors import EngineError
from engine.executor import validate_pipeline_dict

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


def _check_access(pipeline: Pipeline, user: User) -> None:
    if user.role != "admin" and pipeline.owner_id != user.id:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Pipeline not found")


def _validate_config(config: dict[str, Any]) -> dict[str, Any]:
    try:
        doc = validate_pipeline_dict(config)
    except EngineError as exc:
        details = getattr(exc, "details", None) or {"error": str(exc)}
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_pipeline_config",
            f"Invalid pipeline configuration: {exc}",
            details,
        ) from exc
    return doc.pipeline.model_dump(mode="json", by_alias=True)


def _detail(pipeline: Pipeline, config: dict[str, Any]) -> PipelineDetailResponse:
    return PipelineDetailResponse(
        id=pipeline.id,
        name=pipeline.name,
        owner_id=pipeline.owner_id,
        current_version=pipeline.current_version,
        created_at=pipeline.created_at,
        updated_at=pipeline.updated_at,
        config=config,
    )


@router.get("", response_model=Page[PipelineResponse])
def list_pipelines(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    principal: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> Page[PipelineResponse]:
    """List pipelines (paginated; users see only their own, admins see all)."""
    user = principal.user
    query = db.query(Pipeline)
    if user.role != "admin":
        query = query.filter(Pipeline.owner_id == user.id)
    total = query.count()
    rows = (
        query.order_by(Pipeline.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return Page[PipelineResponse](
        items=[PipelineResponse.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=PipelineDetailResponse, status_code=status.HTTP_201_CREATED)
def create_pipeline(
    payload: PipelineCreate,
    request: Request,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> PipelineDetailResponse:
    """Validate the configuration and create a pipeline at version 1."""
    user = principal.user
    inner = _validate_config(payload.config)
    pipeline = Pipeline(owner_id=user.id, name=payload.name, current_version=1)
    db.add(pipeline)
    db.flush()  # assign id before stamping config
    inner["id"] = str(pipeline.id)
    inner["name"] = payload.name
    inner["version"] = 1
    db.add(PipelineVersion(pipeline_id=pipeline.id, version=1, config={"pipeline": inner}))
    db.commit()
    db.refresh(pipeline)

    AuditService.record(
        db,
        actor=user.email,
        action=AuditAction.PIPELINE_CREATE,
        target_type="pipeline",
        target_id=pipeline.id,
        ip=request.client.host if request.client else None,
        metadata={"name": payload.name},
    )

    return _detail(pipeline, {"pipeline": inner})


@router.get("/{pipeline_id}", response_model=PipelineDetailResponse)
def get_pipeline(
    pipeline_id: uuid.UUID,
    principal: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> PipelineDetailResponse:
    """Read a pipeline with its current configuration."""
    user = principal.user
    pipeline = db.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Pipeline not found")
    _check_access(pipeline, user)
    version = (
        db.query(PipelineVersion)
        .filter(
            PipelineVersion.pipeline_id == pipeline.id,
            PipelineVersion.version == pipeline.current_version,
        )
        .first()
    )
    if version is None:
        raise ApiError(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            "Current pipeline version is missing",
        )
    return _detail(pipeline, version.config)


@router.put("/{pipeline_id}", response_model=PipelineDetailResponse)
def update_pipeline(
    pipeline_id: uuid.UUID,
    payload: PipelineUpdate,
    request: Request,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> PipelineDetailResponse:
    """Update name/config; config changes create a new immutable version."""
    user = principal.user
    pipeline = db.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Pipeline not found")
    _check_access(pipeline, user)
    if payload.name is not None:
        pipeline.name = payload.name
    inner: dict[str, Any] | None = None
    if payload.config is not None:
        inner = _validate_config(payload.config)
        new_version = pipeline.current_version + 1
        inner["id"] = str(pipeline.id)
        inner["name"] = pipeline.name
        inner["version"] = new_version
        db.add(
            PipelineVersion(
                pipeline_id=pipeline.id, version=new_version, config={"pipeline": inner}
            )
        )
        pipeline.current_version = new_version
    db.commit()
    db.refresh(pipeline)

    AuditService.record(
        db,
        actor=user.email,
        action=AuditAction.PIPELINE_UPDATE,
        target_type="pipeline",
        target_id=pipeline.id,
        ip=request.client.host if request.client else None,
        metadata={"name": pipeline.name, "new_version": pipeline.current_version},
    )

    if inner is None:
        current = (
            db.query(PipelineVersion)
            .filter(
                PipelineVersion.pipeline_id == pipeline.id,
                PipelineVersion.version == pipeline.current_version,
            )
            .first()
        )
        inner = current.config["pipeline"] if current else {"pipeline": {}}
        return _detail(pipeline, current.config if current else {"pipeline": {}})
    return _detail(pipeline, {"pipeline": inner})


@router.delete("/{pipeline_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pipeline(
    pipeline_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> None:
    """Delete a pipeline and its versions, runs, and logs."""
    user = principal.user
    pipeline = db.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Pipeline not found")
    _check_access(pipeline, user)

    pipeline_name = pipeline.name
    pipeline_id_str = str(pipeline.id)
    db.delete(pipeline)
    db.commit()

    AuditService.record(
        db,
        actor=user.email,
        action=AuditAction.PIPELINE_DELETE,
        target_type="pipeline",
        target_id=uuid.UUID(pipeline_id_str),
        ip=request.client.host if request.client else None,
        metadata={"name": pipeline_name},
    )


@router.get("/{pipeline_id}/versions", response_model=Page[PipelineVersionResponse])
def list_versions(
    pipeline_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    principal: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> Page[PipelineVersionResponse]:
    """List preserved configuration versions (history is never overwritten)."""
    user = principal.user
    pipeline = db.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Pipeline not found")
    _check_access(pipeline, user)
    query = db.query(PipelineVersion).filter(PipelineVersion.pipeline_id == pipeline.id)
    total = query.count()
    rows = (
        query.order_by(PipelineVersion.version.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return Page[PipelineVersionResponse](
        items=[PipelineVersionResponse.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )
