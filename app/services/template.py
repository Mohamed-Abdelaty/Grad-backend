"""Template service: CRUD, seeding, visibility, use/clone, publish."""

from __future__ import annotations

import copy
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.models.entities import PipelineVersion, Template, User
from app.schemas.template import TemplateCreate, TemplateUpdate
from app.services.audit import AuditAction, AuditService

logger = logging.getLogger("chaosflow.templates")


class TemplateService:
    """Service for managing pipeline templates."""

    @staticmethod
    def create_template(
        db: Session,
        user: User,
        payload: TemplateCreate,
        request_ip: str | None = None,
    ) -> Template:
        """Create a user template (private by default)."""
        template = Template(
            name=payload.name,
            category=payload.category,
            description=payload.description,
            config=payload.config,
            owner_id=user.id,
            visibility="private",
            is_published=False,
        )
        db.add(template)
        db.commit()
        db.refresh(template)

        AuditService.record(
            db,
            actor=user.email,
            action=AuditAction.TEMPLATE_CREATE,
            target_type="template",
            target_id=template.id,
            ip=request_ip,
            metadata={"name": payload.name, "category": payload.category},
        )

        return template

    @staticmethod
    def list_templates(
        db: Session,
        user: User,
        q: str | None = None,
        category: str | None = None,
        scope: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Template], int]:
        """List templates visible to the user."""
        query = db.query(Template)

        # Visibility: users see public + own private templates
        if user.role != "admin":
            query = query.filter(
                (Template.visibility == "public") | (Template.owner_id == user.id)
            )

        # Scope filter
        if scope == "mine":
            query = query.filter(Template.owner_id == user.id)
        elif scope == "public":
            query = query.filter(Template.visibility == "public")

        # Search
        if q:
            search_term = f"%{q}%"
            query = query.filter(
                Template.name.ilike(search_term) | Template.description.ilike(search_term)
            )

        # Category filter
        if category:
            query = query.filter(Template.category == category)

        total = query.count()
        templates = (
            query.order_by(Template.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return templates, total

    @staticmethod
    def get_template(db: Session, template_id: UUID, user: User) -> Template | None:
        """Get a template if user has access."""
        template = db.query(Template).filter(Template.id == template_id).first()
        if not template:
            return None

        # Check visibility
        if template.visibility == "private" and template.owner_id != user.id and user.role != "admin":
            return None

        return template

    @staticmethod
    def update_template(
        db: Session,
        user: User,
        template_id: UUID,
        payload: TemplateUpdate,
        request_ip: str | None = None,
    ) -> Template:
        """Update a template (only owner can update private templates)."""
        template = db.query(Template).filter(Template.id == template_id).first()
        if not template:
            raise ApiError(404, "not_found", "Template not found")

        # Check permissions
        if template.owner_id != user.id and user.role != "admin":
            raise ApiError(404, "not_found", "Template not found")

        if payload.name is not None:
            template.name = payload.name
        if payload.category is not None:
            template.category = payload.category
        if payload.description is not None:
            template.description = payload.description
        if payload.config is not None:
            template.config = payload.config
        if payload.visibility is not None:
            # Only admin can publish
            if payload.visibility == "public" and user.role != "admin":
                raise ApiError(403, "forbidden", "Only admins can publish templates")
            template.visibility = payload.visibility
            template.is_published = (payload.visibility == "public")

        db.commit()
        db.refresh(template)

        AuditService.record(
            db,
            actor=user.email,
            action=AuditAction.TEMPLATE_UPDATE,
            target_type="template",
            target_id=template.id,
            ip=request_ip,
            metadata={"name": template.name},
        )

        return template

    @staticmethod
    def delete_template(
        db: Session,
        user: User,
        template_id: UUID,
        request_ip: str | None = None,
    ) -> None:
        """Delete a template (only owner can delete private, admin can delete any)."""
        template = db.query(Template).filter(Template.id == template_id).first()
        if not template:
            raise ApiError(404, "not_found", "Template not found")

        if template.owner_id != user.id and user.role != "admin":
            raise ApiError(404, "not_found", "Template not found")

        template_name = template.name
        db.delete(template)
        db.commit()

        AuditService.record(
            db,
            actor=user.email,
            action=AuditAction.TEMPLATE_DELETE,
            target_type="template",
            target_id=template_id,
            ip=request_ip,
            metadata={"name": template_name},
        )

    @staticmethod
    def use_template(
        db: Session,
        user: User,
        template_id: UUID,
        request_ip: str | None = None,
    ) -> tuple[Template, dict[str, Any]]:
        """Clone a template into a new pipeline owned by the user."""
        template = TemplateService.get_template(db, template_id, user)
        if not template:
            raise ApiError(404, "not_found", "Template not found")

        # Increment usage count atomically
        db.query(Template).filter(Template.id == template_id).update(
            {Template.usage_count: Template.usage_count + 1}
        )
        db.commit()
        db.refresh(template)

        # Create pipeline from template config
        from app.models.entities import Pipeline, PipelineVersion

        pipeline = Pipeline(owner_id=user.id, name=template.name, current_version=1)
        db.add(pipeline)
        db.flush()

        # Deep copy config to prevent mutating template
        config = copy.deepcopy(template.config)
        config["pipeline"]["id"] = str(pipeline.id)
        config["pipeline"]["name"] = template.name
        config["pipeline"]["version"] = 1

        db.add(PipelineVersion(pipeline_id=pipeline.id, version=1, config=config))
        db.commit()
        db.refresh(pipeline)

        AuditService.record(
            db,
            actor=user.email,
            action=AuditAction.TEMPLATE_USE,
            target_type="template",
            target_id=template.id,
            ip=request_ip,
            metadata={"name": template.name, "pipeline_id": str(pipeline.id)},
        )

        return template, config["pipeline"]

    @staticmethod
    def save_as_template(
        db: Session,
        user: User,
        pipeline_id: UUID,
        name: str,
        category: str,
        description: str,
        request_ip: str | None = None,
    ) -> Template:
        """Save an existing pipeline as a private template."""
        from app.models.entities import Pipeline

        pipeline = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
        if not pipeline:
            raise ApiError(404, "not_found", "Pipeline not found")

        if pipeline.owner_id != user.id and user.role != "admin":
            raise ApiError(404, "not_found", "Pipeline not found")

        # Get current pipeline version
        version = db.query(PipelineVersion).filter(
            PipelineVersion.pipeline_id == pipeline.id,
            PipelineVersion.version == pipeline.current_version,
        ).first()
        if not version:
            raise ApiError(500, "internal_error", "Pipeline version not found")

        template = Template(
            name=name,
            category=category,
            description=description,
            config=version.config,
            owner_id=user.id,
            visibility="private",
            is_published=False,
        )
        db.add(template)
        db.commit()
        db.refresh(template)

        AuditService.record(
            db,
            actor=user.email,
            action=AuditAction.TEMPLATE_CREATE,
            target_type="template",
            target_id=template.id,
            ip=request_ip,
            metadata={"name": name, "pipeline_id": str(pipeline_id)},
        )

        return template

    @staticmethod
    def publish_template(
        db: Session,
        user: User,
        template_id: UUID,
        request_ip: str | None = None,
    ) -> Template:
        """Publish a template (make it public) - admin only."""
        if user.role != "admin":
            raise ApiError(403, "forbidden", "Only admins can publish templates")

        template = db.query(Template).filter(Template.id == template_id).first()
        if not template:
            raise ApiError(404, "not_found", "Template not found")

        template.visibility = "public"
        template.is_published = True
        db.commit()
        db.refresh(template)

        AuditService.record(
            db,
            actor=user.email,
            action=AuditAction.TEMPLATE_PUBLISH,
            target_type="template",
            target_id=template.id,
            ip=request_ip,
            metadata={"name": template.name},
        )

        return template


# Seed templates - these MUST be valid pipeline configs
SEED_TEMPLATES: list[dict[str, Any]] = [
    {
        "name": "CSV to PostgreSQL Cleanup",
        "category": "ETL",
        "description": "Load CSV data, filter and clean, then upsert into PostgreSQL",
        "config": {
            "pipeline": {
                "id": "00000000-0000-0000-0000-000000000001",
                "name": "CSV to PostgreSQL Cleanup",
                "version": 1,
                "nodes": [
                    {
                        "id": "src_csv",
                        "type": "source",
                        "plugin": "csv",
                        "config": {"path": "data/orders.csv"}
                    },
                    {
                        "id": "tr_filter",
                        "type": "transform",
                        "plugin": "filter",
                        "config": {"expression": "amount > 0 and status == 'paid'"}
                    },
                    {
                        "id": "tr_cast",
                        "type": "transform",
                        "plugin": "cast",
                        "config": {"fields": {"amount": "float", "customer_id": "int"}, "on_error": "skip"}
                    },
                    {
                        "id": "snk_pg",
                        "type": "sink",
                        "plugin": "postgres",
                        "config": {
                            "dsn_env": "DATABASE_URL",
                            "table": "orders_clean",
                            "mode": "upsert",
                            "conflict_keys": ["transaction_id"]
                        }
                    }
                ],
                "edges": [
                    {"from": "src_csv", "to": "tr_filter"},
                    {"from": "tr_filter", "to": "tr_cast"},
                    {"from": "tr_cast", "to": "snk_pg"}
                ],
                "retry": {"max_attempts": 3, "backoff_seconds": 5}
            }
        }
    },
    {
        "name": "API to JSON Snapshot",
        "category": "Sync",
        "description": "Fetch data from REST API and save as JSON snapshot",
        "config": {
            "pipeline": {
                "id": "00000000-0000-0000-0000-000000000002",
                "name": "API to JSON Snapshot",
                "version": 1,
                "nodes": [
                    {
                        "id": "src_api",
                        "type": "source",
                        "plugin": "rest_api",
                        "config": {
                            "url": "https://api.example.com/data",
                            "method": "GET",
                            "headers": {"Accept": "application/json"},
                            "pagination": {"type": "page", "page_param": "page", "page_size": 100},
                            "max_pages": 10,
                            "timeout_seconds": 30
                        }
                    },
                    {
                        "id": "snk_json",
                        "type": "sink",
                        "plugin": "json",
                        "config": {"path": "output/api_snapshot.json", "mode": "overwrite"}
                    }
                ],
                "edges": [
                    {"from": "src_api", "to": "snk_json"}
                ],
                "retry": {"max_attempts": 3, "backoff_seconds": 10}
            }
        }
    },
    {
        "name": "Deduplicate and Aggregate",
        "category": "Analytics",
        "description": "Deduplicate records by key, then aggregate metrics by group",
        "config": {
            "pipeline": {
                "id": "00000000-0000-0000-0000-000000000003",
                "name": "Deduplicate and Aggregate",
                "version": 1,
                "nodes": [
                    {
                        "id": "src_json",
                        "type": "source",
                        "plugin": "json",
                        "config": {"path": "data/events.json"}
                    },
                    {
                        "id": "tr_dedup",
                        "type": "transform",
                        "plugin": "deduplicate",
                        "config": {"keys": ["event_id"]}
                    },
                    {
                        "id": "tr_agg",
                        "type": "transform",
                        "plugin": "aggregate",
                        "config": {
                            "group_by": ["user_id", "event_type"],
                            "aggregations": [
                                {"field": "event_id", "op": "count", "as": "count"},
                                {"field": "amount", "op": "sum", "as": "total_amount"}
                            ]
                        }
                    },
                    {
                        "id": "snk_console",
                        "type": "sink",
                        "plugin": "console",
                        "config": {}
                    }
                ],
                "edges": [
                    {"from": "src_json", "to": "tr_dedup"},
                    {"from": "tr_dedup", "to": "tr_agg"},
                    {"from": "tr_agg", "to": "snk_console"}
                ],
                "retry": {"max_attempts": 2, "backoff_seconds": 5}
            }
        }
    },
    {
        "name": "JSON to CSV Export",
        "category": "ETL",
        "description": "Convert JSON Lines data to CSV format",
        "config": {
            "pipeline": {
                "id": "00000000-0000-0000-0000-000000000004",
                "name": "JSON to CSV Export",
                "version": 1,
                "nodes": [
                    {
                        "id": "src_json",
                        "type": "source",
                        "plugin": "json",
                        "config": {"path": "data/input.jsonl"}
                    },
                    {
                        "id": "snk_csv",
                        "type": "sink",
                        "plugin": "csv",
                        "config": {"path": "output/export.csv", "mode": "overwrite"}
                    }
                ],
                "edges": [
                    {"from": "src_json", "to": "snk_csv"}
                ],
                "retry": {"max_attempts": 1, "backoff_seconds": 0}
            }
        }
    },
    {
        "name": "Filter and Rename to Console",
        "category": "Analytics",
        "description": "Filter records, rename columns, output to console",
        "config": {
            "pipeline": {
                "id": "00000000-0000-0000-0000-000000000005",
                "name": "Filter and Rename to Console",
                "version": 1,
                "nodes": [
                    {
                        "id": "src_csv",
                        "type": "source",
                        "plugin": "csv",
                        "config": {"path": "data/orders.csv"}
                    },
                    {
                        "id": "tr_filter",
                        "type": "transform",
                        "plugin": "filter",
                        "config": {"expression": "amount > 100"}
                    },
                    {
                        "id": "tr_map",
                        "type": "transform",
                        "plugin": "map",
                        "config": {
                            "mapping": {"transaction_id": "txn_id", "customer_id": "cust_id", "amount": "amount"}
                        }
                    },
                    {
                        "id": "snk_console",
                        "type": "sink",
                        "plugin": "console",
                        "config": {}
                    }
                ],
                "edges": [
                    {"from": "src_csv", "to": "tr_filter"},
                    {"from": "tr_filter", "to": "tr_map"},
                    {"from": "tr_map", "to": "snk_console"}
                ],
                "retry": {"max_attempts": 1, "backoff_seconds": 0}
            }
        }
    },
    {
        "name": "PostgreSQL to CSV Export",
        "category": "ETL",
        "description": "Query PostgreSQL and export results to CSV",
        "config": {
            "pipeline": {
                "id": "00000000-0000-0000-0000-000000000006",
                "name": "PostgreSQL to CSV Export",
                "version": 1,
                "nodes": [
                    {
                        "id": "src_pg",
                        "type": "source",
                        "plugin": "postgres",
                        "config": {
                            "dsn_env": "DATABASE_URL",
                            "query": "SELECT * FROM orders WHERE created_at > NOW() - INTERVAL '30 days'"
                        }
                    },
                    {
                        "id": "snk_csv",
                        "type": "sink",
                        "plugin": "csv",
                        "config": {"path": "output/orders_export.csv", "mode": "overwrite"}
                    }
                ],
                "edges": [
                    {"from": "src_pg", "to": "snk_csv"}
                ],
                "retry": {"max_attempts": 2, "backoff_seconds": 5}
            }
        }
    }
]


def seed_templates(db: Session) -> int:
    """Seed system templates (idempotent)."""
    seeded = 0
    for template_data in SEED_TEMPLATES:
        # Check if template already exists
        existing = db.query(Template).filter(
            Template.name == template_data["name"],
            Template.owner_id.is_(None),  # System templates have no owner
        ).first()

        if existing:
            # Update config to ensure it's valid
            existing.config = template_data["config"]
            existing.description = template_data["description"]
            existing.category = template_data["category"]
            existing.is_published = True
            existing.visibility = "public"
            db.commit()
        else:
            template = Template(
                name=template_data["name"],
                category=template_data["category"],
                description=template_data["description"],
                config=template_data["config"],
                owner_id=None,  # System template
                visibility="public",
                is_published=True,
            )
            db.add(template)
            seeded += 1

    db.commit()
    logger.info("Seeded %d system templates", seeded)
    return seeded


def validate_seed_templates() -> list[str]:
    """Validate all seed templates against the pipeline validator."""
    from engine.errors import EngineError
    from engine.executor import validate_pipeline_dict

    errors = []
    for i, template_data in enumerate(SEED_TEMPLATES):
        try:
            validate_pipeline_dict(template_data["config"])
        except EngineError as exc:
            errors.append(f"Template {i+1} ({template_data['name']}): {exc}")
    return errors
