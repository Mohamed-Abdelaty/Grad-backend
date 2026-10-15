"""SQLAlchemy 2.x models: User, Pipeline, PipelineVersion, Run, RunLog.

Conventions: UUID primary keys, UTC timestamps, foreign keys with indexes,
and immutable pipeline versions (history is preserved, never overwritten).
Each run references the exact pipeline version it executed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

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
