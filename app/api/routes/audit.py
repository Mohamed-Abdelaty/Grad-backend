"""Audit log endpoints (admin only)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_principal
from app.core.errors import ApiError
from app.db.session import get_db
from app.schemas.audit import AuditEventResponse, AuditLogFilters, AuditLogListResponse
from app.services.apikey import Principal
from app.services.audit import AuditService

router = APIRouter(prefix="/admin/audit-log", tags=["admin-audit-log"])


@router.get("", response_model=AuditLogListResponse)
def list_audit_logs(
    filters: AuditLogFilters = Depends(),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> AuditLogListResponse:
    """List audit events with filters (admin only)."""
    if principal.auth_method == "apikey":
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "forbidden",
            "API keys cannot access audit log",
        )
    # FIX F-02: require admin role for JWT users too
    if principal.user.role != "admin":
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "forbidden",
            "Admin role required",
        )

    events, total = AuditService.list_events(
        db,
        actor=filters.actor,
        action=filters.action,
        target_type=filters.target_type,
        target_id=filters.target_id,
        from_=filters.from_,
        to=filters.to,
        page=filters.page,
        page_size=filters.page_size,
    )

    return AuditLogListResponse(
        items=[AuditEventResponse.model_validate(e) for e in events],
        total=total,
        page=filters.page,
        page_size=filters.page_size,
    )


@router.get("/export")
def export_audit_logs_csv(
    actor: str | None = Query(default=None),
    action: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    target_id: UUID | None = Query(default=None),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> Response:
    """Export audit events as CSV (admin only)."""
    if principal.auth_method == "apikey":
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "forbidden",
            "API keys cannot export audit log",
        )
    if principal.user.role != "admin":
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "forbidden",
            "Admin role required",
        )

    csv_content = AuditService.export_csv(
        db,
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        from_=from_,
        to=to,
    )

    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit_log.csv"'},
    )
