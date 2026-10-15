"""Initial schema: users, pipelines, versions, runs, logs, refresh tokens."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_role", "users", ["role"])

    op.create_table(
        "pipelines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_pipelines_owner_id", "pipelines", ["owner_id"])
    op.create_index("ix_pipelines_name", "pipelines", ["name"])

    op.create_table(
        "pipeline_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("pipeline_id", sa.Uuid(), sa.ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("pipeline_id", "version", name="uq_pipeline_version"),
    )
    op.create_index("ix_pipeline_versions_pipeline_id", "pipeline_versions", ["pipeline_id"])
    op.create_index("ix_pipeline_versions_version", "pipeline_versions", ["version"])

    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("pipeline_id", sa.Uuid(), sa.ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pipeline_version_id", sa.Uuid(),
                  sa.ForeignKey("pipeline_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("records_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_type", sa.String(128), nullable=True),
        sa.Column("failed_node_id", sa.String(128), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_runs_pipeline_id", "runs", ["pipeline_id"])
    op.create_index("ix_runs_pipeline_version_id", "runs", ["pipeline_version_id"])
    op.create_index("ix_runs_owner_id", "runs", ["owner_id"])
    op.create_index("ix_runs_status", "runs", ["status"])

    op.create_table(
        "run_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level", sa.String(16), nullable=False, server_default="INFO"),
        sa.Column("node_id", sa.String(128), nullable=True),
        sa.Column("event", sa.String(32), nullable=True),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("records_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_out", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_run_logs_run_id", "run_logs", ["run_id"])
    op.create_index("ix_run_logs_timestamp", "run_logs", ["timestamp"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("jti", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_jti", "refresh_tokens", ["jti"], unique=True)


def downgrade() -> None:
    op.drop_table("refresh_tokens")
    op.drop_table("run_logs")
    op.drop_table("runs")
    op.drop_table("pipeline_versions")
    op.drop_table("pipelines")
    op.drop_table("users")
