"""SQLAlchemy 2.x models: User, Pipeline, PipelineVersion, Run, RunLog,
Schedule, Notification, Template, AuditEvent, ApiKey, NotificationPreference, NotificationDelivery.

Conventions: UUID primary keys, UTC timestamps, foreign keys with indexes,
and immutable pipeline versions (history is preserved, never overwritten).
Each run references the exact pipeline version it executed.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _hash_key(key: str) -> str:
    """SHA256 hash of the full API key."""
    return hashlib.sha256(key.encode()).hexdigest()


def _generate_prefix() -> str:
    """Generate 8-char prefix for API key."""
    return secrets.token_urlsafe(6)[:8]


def _generate_secret() -> str:
    """Generate 32+ byte URL-safe secret for API key."""
    return secrets.token_urlsafe(32)


class User(Base):
    """Application account (roles: ``admin`` | ``user``)."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    pipelines: Mapped[list[Pipeline]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Pipeline(Base):
    """Pipeline identity + pointer to the current version."""

    __tablename__ = "pipelines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(256), index=True)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    owner: Mapped[User] = relationship(back_populates="pipelines")
    versions: Mapped[list[PipelineVersion]] = relationship(
        back_populates="pipeline", cascade="all, delete-orphan", order_by="PipelineVersion.version"
    )
    runs: Mapped[list[Run]] = relationship(back_populates="pipeline", cascade="all, delete-orphan")


class PipelineVersion(Base):
    """Immutable snapshot of a pipeline configuration (JSON document)."""

    __tablename__ = "pipeline_versions"
    __table_args__ = (UniqueConstraint("pipeline_id", "version", name="uq_pipeline_version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pipelines.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, index=True)
    config: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    pipeline: Mapped[Pipeline] = relationship(back_populates="versions")


class Run(Base):
    """One execution of a specific pipeline version (background worker)."""

    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pipelines.id", ondelete="CASCADE"), index=True
    )
    pipeline_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pipeline_versions.id", ondelete="RESTRICT"), index=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    trigger: Mapped[str] = mapped_column(String(16), default="manual", index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    records_in: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    records_out: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    failed_node_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    run_metrics: Mapped[dict | None] = mapped_column("metrics", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    pipeline: Mapped[Pipeline] = relationship(back_populates="runs")
    logs: Mapped[list[RunLog]] = relationship(back_populates="run", cascade="all, delete-orphan")


TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


class RunLog(Base):
    """Persisted execution log/event row (survives WebSocket disconnects)."""

    __tablename__ = "run_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    level: Mapped[str] = mapped_column(String(16), default="INFO")
    node_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    event: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")
    records_in: Mapped[int] = mapped_column(Integer, default=0)
    records_out: Mapped[int] = mapped_column(Integer, default=0)

    run: Mapped[Run] = relationship(back_populates="logs")


class RefreshToken(Base):
    """Server-side refresh-token record enabling rotation and revocation."""

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped[User] = relationship(back_populates="refresh_tokens")


class Schedule(Base):
    """Pipeline schedule (cron-based)."""

    __tablename__ = "schedules"
    __table_args__ = (UniqueConstraint("pipeline_id", name="uq_schedule_pipeline"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pipelines.id", ondelete="CASCADE"), index=True, unique=True
    )
    cron: Mapped[str] = mapped_column(String(128))
    timezone: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    misfire_policy: Mapped[str] = mapped_column(String(16), default="skip")
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    pipeline: Mapped[Pipeline] = relationship(back_populates="schedule")


class NotificationType(PyEnum):
    RUN_FAILED = "run_failed"
    RUN_SUCCEEDED = "run_succeeded"
    SCHEDULE_MISSED = "schedule_missed"


class Notification(Base):
    """In-app notification for a user."""

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="SET NULL"), index=True, nullable=True
    )
    read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)

    user: Mapped[User] = relationship(back_populates="notifications")
    run: Mapped[Run | None] = relationship()


class TemplateCategory(PyEnum):
    ETL = "ETL"
    ANALYTICS = "Analytics"
    SYNC = "Sync"


class TemplateVisibility(PyEnum):
    PRIVATE = "private"
    PUBLIC = "public"


class Template(Base):
    """Pipeline template (system-seeded or user-created)."""

    __tablename__ = "templates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), index=True)
    category: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSON)
    usage_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    owner: Mapped[User | None] = relationship()


class AuditEvent(Base):
    """Immutable audit log entry."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    actor: Mapped[str] = mapped_column(String(320), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class ApiKey(Base):
    """API key for programmatic access (hash-only storage)."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    prefix: Mapped[str] = mapped_column(String(16), index=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped[User] = relationship(back_populates="api_keys")


class ChannelType(PyEnum):
    IN_APP = "in_app"
    EMAIL = "email"
    WEBHOOK = "webhook"


class NotificationPreference(Base):
    """Per-user notification preferences per event type and channel."""

    __tablename__ = "notification_preferences"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    run_failed_in_app: Mapped[bool] = mapped_column(Boolean, default=True)
    run_failed_email: Mapped[bool] = mapped_column(Boolean, default=False)
    run_failed_webhook: Mapped[bool] = mapped_column(Boolean, default=False)
    run_succeeded_in_app: Mapped[bool] = mapped_column(Boolean, default=True)
    run_succeeded_email: Mapped[bool] = mapped_column(Boolean, default=False)
    run_succeeded_webhook: Mapped[bool] = mapped_column(Boolean, default=False)
    schedule_missed_in_app: Mapped[bool] = mapped_column(Boolean, default=True)
    schedule_missed_email: Mapped[bool] = mapped_column(Boolean, default=False)
    schedule_missed_webhook: Mapped[bool] = mapped_column(Boolean, default=False)
    webhook_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    webhook_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    user: Mapped[User] = relationship(back_populates="notification_preferences")


class DeliveryStatus(PyEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class NotificationDelivery(Base):
    """Delivery attempt log for notifications (email/webhook)."""

    __tablename__ = "notification_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    notification_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("notifications.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(16))
    target: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    notification: Mapped[Notification] = relationship(back_populates="deliveries")


# Add relationships to User
User.notifications = relationship(
    "Notification", back_populates="user", cascade="all, delete-orphan"
)
User.api_keys = relationship("ApiKey", back_populates="user", cascade="all, delete-orphan")
User.notification_preferences = relationship(
    "NotificationPreference", back_populates="user", cascade="all, delete-orphan"
)
Pipeline.schedule = relationship(
    "Schedule", back_populates="pipeline", cascade="all, delete-orphan", uselist=False
)
Notification.deliveries = relationship(
    "NotificationDelivery", back_populates="notification", cascade="all, delete-orphan"
)
