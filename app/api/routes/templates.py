"""Template management endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_scope
from app.core.errors import ApiError
from app.db.session import get_db
from app.models.entities import User
from app.schemas.template import (
    TemplateCreate,
    TemplateListResponse,
    TemplateResponse,
    TemplateUpdate,
    TemplateUseResponse,
)
from app.services.apikey import Principal
from app.services.template import TemplateService

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=TemplateListResponse)
def list_templates(
    q: str | None = Query(default=None, description="Search in name and description"),
    category: str | None = Query(default=None, description="Filter by category (ETL|Analytics|Sync)"),
    scope: str | None = Query(default=None, description="Scope filter: mine|public"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TemplateListResponse:
    """List templates (public + own private; filtered by category, search)."""
    templates, total = TemplateService.list_templates(
        db, user, q=q, category=category, scope=scope, page=page, page_size=page_size
    )
    return TemplateListResponse(
        items=[TemplateResponse.model_validate(t) for t in templates],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{template_id}", response_model=TemplateResponse)
def get_template(
    template_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TemplateResponse:
    """Get a template by ID (if visible to user)."""
    template = TemplateService.get_template(db, template_id, user)
    if template is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Template not found")
    return TemplateResponse.model_validate(template)


@router.post("", response_model=TemplateResponse, status_code=status.HTTP_201_CREATED)
def create_template(
    payload: TemplateCreate,
    request: Request,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> TemplateResponse:
    """Create a private template from a pipeline config."""
    user = principal.user
    template = TemplateService.create_template(db, user, payload, request.client.host if request.client else None)
    return TemplateResponse.model_validate(template)


@router.put("/{template_id}", response_model=TemplateResponse)
def update_template(
    template_id: UUID,
    payload: TemplateUpdate,
    request: Request,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> TemplateResponse:
    """Update a template (only owner can update private templates)."""
    user = principal.user
    template = TemplateService.update_template(db, user, template_id, payload, request.client.host if request.client else None)
    return TemplateResponse.model_validate(template)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(
    template_id: UUID,
    request: Request,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> None:
    """Delete a template (only owner can delete private templates)."""
    user = principal.user
    TemplateService.delete_template(db, user, template_id, request.client.host if request.client else None)


@router.post("/{template_id}/use", response_model=TemplateUseResponse)
def use_template(
    template_id: UUID,
    request: Request,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> TemplateUseResponse:
    """Clone a template into a new pipeline owned by the caller."""
    user = principal.user
    template, pipeline_config = TemplateService.use_template(db, user, template_id, request.client.host if request.client else None)
    # NOTE: pipeline_config["id"] is the new pipeline's UUID (set in use_template)
    return TemplateUseResponse(
        pipeline_id=pipeline_config["id"],  # FIX F-07: was template.id (wrong)
        name=template.name,
        config=pipeline_config,
        version=1,
    )


def _get_client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post(
    "/pipelines/{pipeline_id}/save-as-template",
    response_model=TemplateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Save Pipeline As Template",
    description="Save an existing pipeline's configuration as a private reusable template.",
)
def save_pipeline_as_template(
    pipeline_id: UUID,
    name: str = Query(..., min_length=1, max_length=256, examples=["Saved Template"], description="Name of template"),
    category: str = Query(..., pattern="^(ETL|Analytics|Sync)$", examples=["ETL"], description="Category: ETL, Analytics, or Sync"),
    description: str = Query(..., min_length=1, max_length=1024, examples=["Template cloned from orders-pipeline"], description="Template description"),
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
    client_ip: str | None = Depends(_get_client_ip),
) -> TemplateResponse:
    """Save an existing pipeline as a private template."""
    user = principal.user
    template = TemplateService.save_as_template(
        db, user, pipeline_id, name, category, description, client_ip
    )
    return TemplateResponse.model_validate(template)


@router.post("/{template_id}/publish", response_model=TemplateResponse)
def publish_template(
    template_id: UUID,
    request: Request,
    principal: Principal = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> TemplateResponse:
    """Publish a template (make it public) - admin only."""
    user = principal.user
    template = TemplateService.publish_template(db, user, template_id, request.client.host if request.client else None)
    return TemplateResponse.model_validate(template)
