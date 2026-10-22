"""Audit log service: append-only recording with DB-level protection."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.entities import AuditEvent

logger = logging.getLogger("chaosflow.audit")


class AuditAction:
    """Audit action constants."""

    AUTH_LOGIN_SUCCESS = "auth.login_success"
    AUTH_LOGIN_FAILED = "auth.login_failed"
    PIPELINE_CREATE = "pipeline.create"
    PIPELINE_UPDATE = "pipeline.update"
    PIPELINE_DELETE = "pipeline.delete"
    PIPELINE_ROLLBACK = "pipeline.rollback"
    RUN_START = "run.start"
    RUN_STOP = "run.stop"
    APIKEY_CREATE = "apikey.create"
    APIKEY_REVOKE = "apikey.revoke"
    USER_ROLE_CHANGE = "user.role_change"
    SCHEDULE_CREATE = "schedule.create"
    SCHEDULE_UPDATE = "schedule.update"
    SCHEDULE_DELETE = "schedule.delete"
    TEMPLATE_PUBLISH = "template.publish"
    TEMPLATE_CREATE = "template.create"
    TEMPLATE_UPDATE = "template.update"
    TEMPLATE_USE = "template.use"
    TEMPLATE_DELETE = "template.delete"


class AuditService:
    """Service for recording immutable audit events."""

    @staticmethod
    def record(
        db: Session,
        actor: str,
        action: str,
        target_type: str | None = None,
        target_id: UUID | None = None,
        ip: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent | None:
        """Record an audit event. Never raises - logs failure but doesn't hide original error."""
        try:
            # Sanitize metadata - never store secrets
            safe_metadata = None
            if metadata:
                safe_metadata = AuditService._sanitize_metadata(metadata)

            audit_event = AuditEvent(
                actor=actor,
                action=action,
                target_type=target_type,
                target_id=target_id,
                ip=ip,
                event_metadata=safe_metadata,
            )
            db.add(audit_event)
            db.commit()
            db.refresh(audit_event)
            return audit_event
        except Exception as exc:
            # Log the failure but don't raise - audit must not break the main operation
            logger.error("Failed to record audit event: %s", exc, exc_info=True)
            db.rollback()
            return None

    @staticmethod
    def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        """Remove sensitive fields from metadata before storing."""
        sanitized = metadata.copy()
        # Remove any fields that might contain secrets
        secret_keys = {
            "password",
            "password_hash",
            "secret",
            "token",
            "access_token",
            "refresh_token",
            "api_key",
            "key",
            "hash",
            "private_key",
            "webhook_secret",
        }
        for key in list(sanitized.keys()):
            if any(sk in key.lower() for sk in secret_keys):
                sanitized[key] = "[REDACTED]"
        return sanitized

    @staticmethod
    def list_events(
        db: Session,
        actor: str | None = None,
        action: str | None = None,
        target_type: str | None = None,
        target_id: UUID | None = None,
        from_: datetime | None = None,
        to: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AuditEvent], int]:
        """List audit events with filters (admin only)."""
        query = db.query(AuditEvent)

        if actor:
            query = query.filter(AuditEvent.actor.ilike(f"%{actor}%"))
        if action:
            query = query.filter(AuditEvent.action == action)
        if target_type:
            query = query.filter(AuditEvent.target_type == target_type)
        if target_id:
            query = query.filter(AuditEvent.target_id == target_id)
        if from_:
            query = query.filter(AuditEvent.created_at >= from_)
        if to:
            query = query.filter(AuditEvent.created_at <= to)

        total = query.count()
        events = (
            query.order_by(AuditEvent.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return events, total

    @staticmethod
    def export_csv(
        db: Session,
        actor: str | None = None,
        action: str | None = None,
        target_type: str | None = None,
        target_id: UUID | None = None,
        from_: datetime | None = None,
        to: datetime | None = None,
    ) -> str:
        """Export audit events as CSV with formula injection protection."""
        events, _ = AuditService.list_events(
            db,
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            from_=from_,
            to=to,
            page=1,
            page_size=10000,  # Large page for export
        )

        lines = ["id,actor,action,target_type,target_id,ip,created_at,metadata"]
        for event in events:
            # Escape CSV fields and protect against formula injection
            fields = [
                str(event.id),
                AuditService._escape_csv(event.actor),
                AuditService._escape_csv(event.action),
                AuditService._escape_csv(event.target_type or ""),
                str(event.target_id) if event.target_id else "",
                AuditService._escape_csv(event.ip or ""),
                event.created_at.isoformat(),
                AuditService._escape_csv(str(event.event_metadata or "")),
            ]
            lines.append(",".join(fields))
        return "\n".join(lines)

    @staticmethod
    def _escape_csv(value: str) -> str:
        """Escape CSV value and prevent formula injection."""
        # Formula injection protection: prepend ' to cells starting with = + - @ tab CR
        if value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
            value = "'" + value
        # Escape double quotes
        if '"' in value:
            value = value.replace('"', '""')
        # Quote if contains comma, newline, carriage return, or quote
        if any(c in value for c in [",", "\n", "\r", '"']):
            value = f'"{value}"'
        return value


def _create_audit_protection_triggers():
    """Create PostgreSQL triggers to prevent UPDATE/DELETE on audit_events.
    This is a no-op for SQLite (triggers not supported in same way).
    """
    # This will be handled by the migration for PostgreSQL
    pass
