"""Run control: start (202 + background), stop, history, details, logs."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import require_scope
from app.core.errors import ApiError
from app.db.session import get_db
from app.models.entities import TERMINAL_STATUSES, Pipeline, PipelineVersion, Run, RunLog, User
from app.schemas.common import Page
from app.schemas.run import RunLogResponse, RunResponse
from app.services import run_worker
from app.services.apikey import Principal
from app.services.audit import AuditAction, AuditService

router = APIRouter(tags=["runs"])


def _to_response(run: Run) -> RunResponse:
    return RunResponse(
        id=run.id,
        pipeline_id=run.pipeline_id,
        version=run.version,
        status=run.status,
        progress=run.progress or 0.0,
        records_in=run.records_in or 0,
        records_out=run.records_out or 0,
        error=run.error,
        error_type=run.error_type,
        failed_node_id=run.failed_node_id,
        metrics=run.run_metrics,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _check_run_access(run: Run, user: User) -> None:
    if user.role != "admin" and run.owner_id != user.id:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Run not found")


@router.post(
    "/pipelines/{pipeline_id}/runs",
    response_model=RunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_run(
    pipeline_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_scope("run")),
    db: Session = Depends(get_db),
) -> RunResponse:
    """Start a run of the pipeline's current version (returns promptly)."""
    user = principal.user
    pipeline = db.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Pipeline not found")
    if user.role != "admin" and pipeline.owner_id != user.id:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Pipeline not found")
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
    run = Run(
        pipeline_id=pipeline.id,
        pipeline_version_id=version.id,
        owner_id=user.id,
        version=version.version,
        status="queued",
        progress=0.0,
        trigger="manual",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    run_worker.submit_run(str(run.id))  # background thread; HTTP returns now

    AuditService.record(
        db,
        actor=user.email,
        action=AuditAction.RUN_START,
        target_type="run",
        target_id=run.id,
        ip=request.client.host if request.client else None,
        metadata={"pipeline_id": str(pipeline.id), "trigger": "manual"},
    )

    return _to_response(run)


@router.post("/runs/{run_id}/stop", response_model=RunResponse)
def stop_run(
    run_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_scope("run")),
    db: Session = Depends(get_db),
) -> RunResponse:
    """Request cancellation; duplicate stops and terminal runs return 409."""
    user = principal.user
    run = db.get(Run, run_id)
    if run is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Run not found")
    _check_run_access(run, user)
    if run.status in TERMINAL_STATUSES:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "already_terminal",
            f"Run is already {run.status}; stop is a no-op",
        )
    if not run_worker.request_stop(str(run.id)):
        # Worker already finished between the status read and the signal.
        db.refresh(run)
        if run.status in TERMINAL_STATUSES:
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "already_terminal",
                f"Run is already {run.status}; stop is a no-op",
            )
    db.refresh(run)

    AuditService.record(
        db,
        actor=user.email,
        action=AuditAction.RUN_STOP,
        target_type="run",
        target_id=run.id,
        ip=request.client.host if request.client else None,
        metadata={"pipeline_id": str(run.pipeline_id)},
    )

    return _to_response(run)


@router.get("/runs", response_model=Page[RunResponse])
def list_runs(
    pipeline_id: uuid.UUID | None = Query(default=None),
    run_status: str | None = Query(default=None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    principal: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> Page[RunResponse]:
    """Run history (paginated; ownership-isolated, admins see all)."""
    user = principal.user
    query = db.query(Run)
    if user.role != "admin":
        query = query.filter(Run.owner_id == user.id)
    if pipeline_id is not None:
        query = query.filter(Run.pipeline_id == pipeline_id)
    if run_status is not None:
        query = query.filter(Run.status == run_status)
    total = query.count()
    rows = (
        query.order_by(Run.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    )
    return Page[RunResponse](
        items=[_to_response(r) for r in rows], total=total, page=page, page_size=page_size
    )


@router.get("/runs/{run_id}", response_model=RunResponse)
def get_run(
    run_id: uuid.UUID,
    principal: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> RunResponse:
    """Run details: status, progress, counters, error, and per-node metrics."""
    user = principal.user
    run = db.get(Run, run_id)
    if run is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Run not found")
    _check_run_access(run, user)
    return _to_response(run)


@router.get("/runs/{run_id}/logs", response_model=Page[RunLogResponse])
def get_run_logs(
    run_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    principal: Principal = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> Page[RunLogResponse]:
    """Persisted run logs (available after WebSocket disconnects)."""
    user = principal.user
    run = db.get(Run, run_id)
    if run is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Run not found")
    _check_run_access(run, user)
    query = db.query(RunLog).filter(RunLog.run_id == run.id)
    total = query.count()
    rows = (
        query.order_by(RunLog.timestamp.asc()).offset((page - 1) * page_size).limit(page_size).all()
    )
    return Page[RunLogResponse](
        items=[RunLogResponse.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )
