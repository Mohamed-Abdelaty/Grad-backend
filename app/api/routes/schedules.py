"""Schedule management endpoints."""

from __future__ import annotations

import contextlib
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_scope
from app.core.errors import ApiError
from app.db.session import get_db
from app.models.entities import Pipeline, Schedule, User
from app.schemas.schedule import (
    ScheduleCreate,
    SchedulePreviewResponse,
    ScheduleResponse,
    ScheduleUpdate,
)
from app.services.apikey import Principal
from app.services.audit import AuditAction, AuditService
from app.services.scheduler import get_scheduler, preview_next_runs

router = APIRouter(prefix="/pipelines", tags=["schedules"])


def _check_pipeline_access(pipeline: Pipeline, user: User) -> None:
    if user.role != "admin" and pipeline.owner_id != user.id:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Pipeline not found")


@router.get("/{pipeline_id}/schedule", response_model=ScheduleResponse)
def get_schedule(
    pipeline_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ScheduleResponse:
    """Get the schedule for a pipeline."""
    pipeline = db.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Pipeline not found")
    _check_pipeline_access(pipeline, user)

    schedule = db.query(Schedule).filter(Schedule.pipeline_id == pipeline_id).first()
    if schedule is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Schedule not found")

    return _to_response(schedule)


@router.post("/{pipeline_id}/schedule", response_model=ScheduleResponse, status_code=status.HTTP_201_CREATED)
def create_schedule(
    pipeline_id: UUID,
    payload: ScheduleCreate,
    request: Request,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> ScheduleResponse:
    """Create a schedule for a pipeline."""
    user = principal.user
    # Scope already enforced by require_scope("write") dependency

    pipeline = db.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Pipeline not found")
    _check_pipeline_access(pipeline, user)

    # Check if schedule already exists
    existing = db.query(Schedule).filter(Schedule.pipeline_id == pipeline_id).first()
    if existing:
        raise ApiError(status.HTTP_409_CONFLICT, "already_exists", "Pipeline already has a schedule")

    # Validate cron and timezone
    try:
        preview_next_runs(payload.cron, payload.timezone, count=1)
    except ValueError as exc:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "validation_error", str(exc)
        ) from exc

    schedule = Schedule(
        pipeline_id=pipeline_id,
        cron=payload.cron,
        timezone=payload.timezone,
        enabled=payload.enabled,
        start_at=payload.start_at,
        end_at=payload.end_at,
        misfire_policy=payload.misfire_policy,
    )
    db.add(schedule)
    db.flush()

    # Compute next run time
    with contextlib.suppress(Exception):
        schedule.next_run_at = preview_next_runs(payload.cron, payload.timezone, count=1)[0]

    db.commit()
    db.refresh(schedule)

    # Reconcile scheduler
    get_scheduler().reconcile()

    AuditService.record(
        db,
        actor=user.email,
        action=AuditAction.SCHEDULE_CREATE,
        target_type="schedule",
        target_id=schedule.id,
        ip=request.client.host if request.client else None,
        metadata={"pipeline_id": str(pipeline_id), "cron": payload.cron},
    )

    return _to_response(schedule)


@router.put("/{pipeline_id}/schedule", response_model=ScheduleResponse)
def update_schedule(
    pipeline_id: UUID,
    payload: ScheduleUpdate,
    request: Request,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> ScheduleResponse:
    """Update a pipeline's schedule."""
    user = principal.user

    pipeline = db.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Pipeline not found")
    _check_pipeline_access(pipeline, user)

    schedule = db.query(Schedule).filter(Schedule.pipeline_id == pipeline_id).first()
    if schedule is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Schedule not found")

    # Validate cron and timezone if provided
    if payload.cron is not None or payload.timezone is not None:
        cron = payload.cron or schedule.cron
        timezone = payload.timezone or schedule.timezone
        try:
            preview_next_runs(cron, timezone, count=1)
        except ValueError as exc:
            raise ApiError(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "validation_error", str(exc)
            ) from exc

    # Update fields
    if payload.cron is not None:
        schedule.cron = payload.cron
    if payload.timezone is not None:
        schedule.timezone = payload.timezone
    if payload.enabled is not None:
        schedule.enabled = payload.enabled
    if payload.start_at is not None:
        schedule.start_at = payload.start_at
    if payload.end_at is not None:
        schedule.end_at = payload.end_at
    if payload.misfire_policy is not None:
        schedule.misfire_policy = payload.misfire_policy

    # Recompute next run time
    with contextlib.suppress(Exception):
        schedule.next_run_at = preview_next_runs(schedule.cron, schedule.timezone, count=1)[0]

    db.commit()
    db.refresh(schedule)

    # Reconcile scheduler
    get_scheduler().reconcile()

    AuditService.record(
        db,
        actor=user.email,
        action=AuditAction.SCHEDULE_UPDATE,
        target_type="schedule",
        target_id=schedule.id,
        ip=request.client.host if request.client else None,
        metadata={"pipeline_id": str(pipeline_id)},
    )

    return _to_response(schedule)


@router.delete("/{pipeline_id}/schedule", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(
    pipeline_id: UUID,
    request: Request,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> None:
    """Delete a pipeline's schedule."""
    user = principal.user

    pipeline = db.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Pipeline not found")
    _check_pipeline_access(pipeline, user)

    schedule = db.query(Schedule).filter(Schedule.pipeline_id == pipeline_id).first()
    if schedule is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Schedule not found")

    schedule_id = schedule.id
    db.delete(schedule)
    db.commit()

    # Reconcile scheduler
    get_scheduler().reconcile()

    AuditService.record(
        db,
        actor=user.email,
        action=AuditAction.SCHEDULE_DELETE,
        target_type="schedule",
        target_id=schedule_id,
        ip=request.client.host if request.client else None,
        metadata={"pipeline_id": str(pipeline_id)},
    )


@router.get(
    "/schedules/preview",
    response_model=SchedulePreviewResponse,
    summary="Preview Schedule Execution Times",
    description="Calculate and preview the next N trigger times for a cron expression and timezone without persisting.",
)
def preview_schedule(
    cron: str = Query(..., min_length=1, max_length=128, description="5-field cron expression, e.g. '0 * * * *'", examples=["0 * * * *"]),
    timezone: str = Query(..., min_length=1, max_length=64, description="IANA timezone, e.g. 'UTC' or 'Africa/Cairo'", examples=["UTC"]),
    count: int = Query(5, ge=1, le=20, description="Number of fire times to compute (1-20)", examples=[5]),
) -> SchedulePreviewResponse:
    """Preview next N fire times for a cron expression."""
    try:
        fire_times = preview_next_runs(cron, timezone, count)
    except ValueError as exc:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "validation_error", str(exc)
        ) from exc
    return SchedulePreviewResponse(fire_times=fire_times)


def _to_response(schedule: Schedule) -> ScheduleResponse:
    """Convert Schedule model to response schema."""
    return ScheduleResponse(
        id=schedule.id,
        pipeline_id=schedule.pipeline_id,
        cron=schedule.cron,
        timezone=schedule.timezone,
        enabled=schedule.enabled,
        start_at=schedule.start_at,
        end_at=schedule.end_at,
        misfire_policy=schedule.misfire_policy,
        next_run_at=schedule.next_run_at,
        last_run_status=schedule.last_run_status,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )
