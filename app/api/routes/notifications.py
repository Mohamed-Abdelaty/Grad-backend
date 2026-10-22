"""Notification endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import ApiError
from app.db.session import get_db
from app.models.entities import Notification, NotificationDelivery, User
from app.schemas.common import Page
from app.schemas.notification import (
    DeliveryLogResponse,
    EmailConfig,
    NotificationListResponse,
    NotificationPreferencesResponse,
    NotificationPreferencesUpdate,
    NotificationReadRequest,
    NotificationResponse,
    WebhookConfig,
    WebhookConfigResponse,
)
from app.services.notification import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationListResponse)
def list_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    unread_only: bool = Query(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationListResponse:
    """List notifications for the current user (paginated)."""
    notifications, total = NotificationService.list_notifications(
        db, user.id, page=page, page_size=page_size, unread_only=unread_only
    )
    unread_count = NotificationService.get_unread_count(db, user.id)
    return NotificationListResponse(
        items=[NotificationResponse.model_validate(n) for n in notifications],
        total=total,
        page=page,
        page_size=page_size,
        unread_count=unread_count,
    )


@router.get("/unread-count")
def get_unread_count(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    """Get unread notification count for the current user."""
    count = NotificationService.get_unread_count(db, user.id)
    return {"unread_count": count}


@router.patch("/{notification_id}/read", response_model=NotificationResponse)
def mark_notification_read(
    notification_id: UUID,
    payload: NotificationReadRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationResponse:
    """Mark a notification as read/unread."""
    notification = NotificationService.mark_as_read(db, user.id, notification_id)
    if notification is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Notification not found")
    return NotificationResponse.model_validate(notification)


@router.patch("/read-all", status_code=status.HTTP_204_NO_CONTENT)
def mark_all_notifications_read(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Mark all notifications as read for the current user."""
    NotificationService.mark_all_as_read(db, user.id)


@router.get("/preferences", response_model=NotificationPreferencesResponse)
def get_notification_preferences(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationPreferencesResponse:
    """Get notification preferences for the current user."""
    prefs = NotificationService.ensure_user_preferences(db, user.id)
    return NotificationPreferencesResponse(
        run_failed={
            "in_app": prefs.run_failed_in_app,
            "email": prefs.run_failed_email,
            "webhook": prefs.run_failed_webhook,
        },
        run_succeeded={
            "in_app": prefs.run_succeeded_in_app,
            "email": prefs.run_succeeded_email,
            "webhook": prefs.run_succeeded_webhook,
        },
        schedule_missed={
            "in_app": prefs.schedule_missed_in_app,
            "email": prefs.schedule_missed_email,
            "webhook": prefs.schedule_missed_webhook,
        },
    )


@router.put("/preferences", response_model=NotificationPreferencesResponse)
def update_notification_preferences(
    payload: NotificationPreferencesUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationPreferencesResponse:
    """Update notification preferences for the current user."""
    prefs = NotificationService.update_preferences(
        db,
        user.id,
        run_failed=payload.run_failed,
        run_succeeded=payload.run_succeeded,
        schedule_missed=payload.schedule_missed,
    )
    return NotificationPreferencesResponse(
        run_failed={
            "in_app": prefs.run_failed_in_app,
            "email": prefs.run_failed_email,
            "webhook": prefs.run_failed_webhook,
        },
        run_succeeded={
            "in_app": prefs.run_succeeded_in_app,
            "email": prefs.run_succeeded_email,
            "webhook": prefs.run_succeeded_webhook,
        },
        schedule_missed={
            "in_app": prefs.schedule_missed_in_app,
            "email": prefs.schedule_missed_email,
            "webhook": prefs.schedule_missed_webhook,
        },
    )


@router.put("/preferences/webhook", response_model=WebhookConfigResponse)
def update_webhook_config(
    payload: WebhookConfig,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WebhookConfigResponse:
    """Update webhook configuration (URL and secret)."""
    prefs = NotificationService.update_webhook_config(db, user.id, payload.url, payload.secret)
    return WebhookConfigResponse(
        url=prefs.webhook_url or "",
        secret="",  # Never return secret after creation
    )


@router.put("/preferences/email", response_model=EmailConfig)
def update_email_config(
    payload: EmailConfig,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EmailConfig:
    """Update email configuration."""
    prefs = NotificationService.update_email_config(db, user.id, payload.email)
    return EmailConfig(email=prefs.email or "")


@router.get("/deliveries", response_model=Page[DeliveryLogResponse])
def list_delivery_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Page[DeliveryLogResponse]:
    """List notification delivery logs for the current user."""
    # Get user's notification IDs
    notification_ids = [n.id for n in db.query(Notification.id).filter(Notification.user_id == user.id).all()]

    query = db.query(NotificationDelivery).filter(
        NotificationDelivery.notification_id.in_(notification_ids)
    )
    total = query.count()
    deliveries = (
        query.order_by(NotificationDelivery.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return Page[DeliveryLogResponse](
        items=[DeliveryLogResponse.model_validate(d) for d in deliveries],
        total=total,
        page=page,
        page_size=page_size,
    )
