"""Notification service: event bus, preferences, email/webhook/in-app delivery."""

from __future__ import annotations

import hashlib
import hmac
import logging
import smtplib
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from email.message import EmailMessage
from uuid import UUID

import httpx
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.entities import (
    Notification,
    NotificationDelivery,
    NotificationPreference,
    User,
)

logger = logging.getLogger("chaosflow.notifications")

_delivery_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="chaosflow-notify")


class NotificationService:
    """Service for managing and delivering notifications."""

    # Event types that can trigger notifications
    EVENT_TYPES = ["run_failed", "run_succeeded", "schedule_missed"]

    @staticmethod
    def _get_fernet() -> Fernet | None:
        """Get Fernet cipher for encrypting webhook secrets."""
        if not settings.FERNET_KEY:
            return None
        try:
            return Fernet(settings.FERNET_KEY.encode())
        except Exception:
            return None

    @staticmethod
    def _encrypt_secret(secret: str) -> str | None:
        """Encrypt a webhook secret."""
        fernet = NotificationService._get_fernet()
        if not fernet:
            return None
        return fernet.encrypt(secret.encode()).decode()

    @staticmethod
    def _decrypt_secret(encrypted: str) -> str | None:
        """Decrypt a webhook secret."""
        fernet = NotificationService._get_fernet()
        if not fernet:
            return None
        try:
            return fernet.decrypt(encrypted.encode()).decode()
        except Exception:
            return None

    @staticmethod
    def _is_private_ip(url: str) -> bool:
        """Check if URL points to private/loopback/link-local/metadata IP (SSRF protection)."""
        try:
            import ipaddress
            from urllib.parse import urlparse

            parsed = urlparse(url)
            host = parsed.hostname
            if not host:
                return True

            # Try to resolve as IP address literal first
            try:
                ip = ipaddress.ip_address(host)
                return (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_reserved
                    or ip.is_unspecified
                )
            except ValueError:
                pass

            # Hostname: resolve DNS to verify it doesn't map to private/internal IPs
            try:
                addr_info = socket.getaddrinfo(host, parsed.port or 80, proto=socket.IPPROTO_TCP)
                for item in addr_info:
                    ip_str = item[4][0]
                    resolved_ip = ipaddress.ip_address(ip_str)
                    if (
                        resolved_ip.is_private
                        or resolved_ip.is_loopback
                        or resolved_ip.is_link_local
                        or resolved_ip.is_reserved
                        or resolved_ip.is_unspecified
                    ):
                        return True
                return False
            except Exception:
                # If DNS fails to resolve, block for SSRF safety
                return True
        except Exception:
            return True  # Block on error

    @staticmethod
    def _generate_webhook_signature(secret: str, timestamp: str, body: bytes) -> str:
        """Generate HMAC-SHA256 signature for webhook."""
        message = f"{timestamp}.{body.decode()}"
        return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def create_notification(
        db: Session,
        user_id: UUID,
        event_type: str,
        title: str,
        body: str,
        run_id: UUID | None = None,
    ) -> Notification:
        """Create an in-app notification."""
        notification = Notification(
            user_id=user_id,
            type=event_type,
            title=title,
            body=body,
            run_id=run_id,
        )
        db.add(notification)
        db.commit()
        db.refresh(notification)
        return notification

    @staticmethod
    def get_user_preferences(db: Session, user_id: UUID) -> NotificationPreference | None:
        """Get notification preferences for a user."""
        return db.query(NotificationPreference).filter(
            NotificationPreference.user_id == user_id
        ).first()

    @staticmethod
    def ensure_user_preferences(db: Session, user_id: UUID) -> NotificationPreference:
        """Get or create default notification preferences for a user."""
        prefs = NotificationService.get_user_preferences(db, user_id)
        if prefs:
            return prefs

        prefs = NotificationPreference(user_id=user_id)
        db.add(prefs)
        db.commit()
        db.refresh(prefs)
        return prefs

    @staticmethod
    def update_preferences(
        db: Session,
        user_id: UUID,
        run_failed: dict[str, bool] | None = None,
        run_succeeded: dict[str, bool] | None = None,
        schedule_missed: dict[str, bool] | None = None,
    ) -> NotificationPreference:
        """Update notification preferences for a user."""
        prefs = NotificationService.ensure_user_preferences(db, user_id)

        if run_failed is not None:
            prefs.run_failed_in_app = run_failed.get("in_app", prefs.run_failed_in_app)
            prefs.run_failed_email = run_failed.get("email", prefs.run_failed_email)
            prefs.run_failed_webhook = run_failed.get("webhook", prefs.run_failed_webhook)

        if run_succeeded is not None:
            prefs.run_succeeded_in_app = run_succeeded.get("in_app", prefs.run_succeeded_in_app)
            prefs.run_succeeded_email = run_succeeded.get("email", prefs.run_succeeded_email)
            prefs.run_succeeded_webhook = run_succeeded.get("webhook", prefs.run_succeeded_webhook)

        if schedule_missed is not None:
            prefs.schedule_missed_in_app = schedule_missed.get("in_app", prefs.schedule_missed_in_app)
            prefs.schedule_missed_email = schedule_missed.get("email", prefs.schedule_missed_email)
            prefs.schedule_missed_webhook = schedule_missed.get("webhook", prefs.schedule_missed_webhook)

        db.commit()
        db.refresh(prefs)
        return prefs

    @staticmethod
    def update_webhook_config(
        db: Session,
        user_id: UUID,
        url: str | None,
        secret: str | None,
    ) -> NotificationPreference:
        """Update webhook configuration for a user."""
        prefs = NotificationService.ensure_user_preferences(db, user_id)
        if url is not None:
            prefs.webhook_url = url
        if secret is not None:
            encrypted = NotificationService._encrypt_secret(secret)
            prefs.webhook_secret_encrypted = encrypted
        db.commit()
        db.refresh(prefs)
        return prefs

    @staticmethod
    def update_email_config(
        db: Session,
        user_id: UUID,
        email: str | None,
    ) -> NotificationPreference:
        """Update email configuration for a user."""
        prefs = NotificationService.ensure_user_preferences(db, user_id)
        if email is not None:
            prefs.email = email
        db.commit()
        db.refresh(prefs)
        return prefs

    @staticmethod
    def _should_deliver(prefs: NotificationPreference, event_type: str, channel: str) -> bool:
        """Check if a notification should be delivered via a channel."""
        attr = f"{event_type}_{channel}"
        return getattr(prefs, attr, False)

    @staticmethod
    def deliver_notification(db: Session, notification: Notification) -> None:
        """Deliver a notification via all enabled channels (fire-and-forget in background)."""
        user = db.get(User, notification.user_id)
        if not user:
            return

        prefs = NotificationService.get_user_preferences(db, user.id)
        if not prefs:
            prefs = NotificationService.ensure_user_preferences(db, user.id)

        event_type = notification.type

        # In-app delivery (always create delivery log and push to WebSocket)
        if NotificationService._should_deliver(prefs, event_type, "in_app"):
            NotificationService._deliver_in_app(db, notification)

        # Email delivery (background, never block caller)
        if NotificationService._should_deliver(prefs, event_type, "email") and prefs.email:
            email_target = prefs.email
            delivery = NotificationDelivery(
                notification_id=notification.id,
                channel="email",
                target=email_target,
                status="pending",
                attempts=0,
            )
            db.add(delivery)
            db.commit()
            db.refresh(delivery)
            _delivery_pool.submit(
                NotificationService._deliver_email_worker,
                str(delivery.id),
                str(notification.id),
                email_target,
            )

        # Webhook delivery (background, never block caller)
        if NotificationService._should_deliver(prefs, event_type, "webhook") and prefs.webhook_url:
            secret = NotificationService._decrypt_secret(prefs.webhook_secret_encrypted or "")
            if secret:
                url_target = prefs.webhook_url
                allow_private = getattr(settings, "SSRF_ALLOW_PRIVATE_IPS", False) or getattr(settings, "WEBHOOK_ALLOW_PRIVATE_IPS", False)
                if not allow_private and NotificationService._is_private_ip(url_target):
                    delivery = NotificationDelivery(
                        notification_id=notification.id,
                        channel="webhook",
                        target=url_target,
                        status="failed",
                        attempts=1,
                        last_error="SSRF protection: private IP blocked",
                        last_attempt_at=datetime.now(UTC),
                    )
                    db.add(delivery)
                    db.commit()
                else:
                    delivery = NotificationDelivery(
                        notification_id=notification.id,
                        channel="webhook",
                        target=url_target,
                        status="pending",
                        attempts=0,
                    )
                    db.add(delivery)
                    db.commit()
                    db.refresh(delivery)
                    _delivery_pool.submit(
                        NotificationService._deliver_webhook_worker,
                        str(delivery.id),
                        str(notification.id),
                        url_target,
                        secret,
                    )

    @staticmethod
    def _deliver_in_app(db: Session, notification: Notification) -> None:
        """Deliver in-app notification (create delivery log and push to WebSocket)."""
        delivery = NotificationDelivery(
            notification_id=notification.id,
            channel="in_app",
            target=f"user:{notification.user_id}",
            status="sent",
            attempts=1,
            last_attempt_at=datetime.now(UTC),
        )
        db.add(delivery)
        db.commit()

        # Push to WebSocket (best effort, fire-and-forget)
        try:
            from app.api.routes.notifications_ws import _notify_user_sync
            _notify_user_sync(notification.user_id, notification)
        except Exception:
            pass

    @staticmethod
    def _deliver_email_worker(delivery_id_str: str, notification_id_str: str, email: str) -> None:
        """Worker thread execution for email delivery with retries."""
        db = SessionLocal()
        try:
            delivery = db.get(NotificationDelivery, UUID(delivery_id_str))
            notification = db.get(Notification, UUID(notification_id_str))
            if not delivery or not notification:
                return

            max_attempts = settings.WEBHOOK_MAX_RETRIES
            for attempt in range(1, max_attempts + 1):
                delivery.attempts = attempt
                delivery.last_attempt_at = datetime.now(UTC)

                try:
                    NotificationService._send_email(email, notification)
                    delivery.status = "sent"
                    delivery.last_error = None
                    db.commit()
                    break
                except Exception as exc:
                    delivery.last_error = str(exc)
                    delivery.status = "failed" if attempt == max_attempts else "pending"
                    db.commit()
                    if attempt < max_attempts:
                        time.sleep(settings.WEBHOOK_BACKOFF_BASE ** attempt)
        except Exception as exc:
            logger.warning("Email delivery worker exception: %s", exc)
        finally:
            db.close()

    @staticmethod
    def _send_email(to_email: str, notification: Notification) -> None:
        """Send email via SMTP."""
        if not settings.SMTP_HOST or not settings.SMTP_FROM:
            raise RuntimeError("SMTP not configured")

        msg = EmailMessage()
        msg["Subject"] = f"[ChaosFlow] {notification.title}"
        msg["From"] = settings.SMTP_FROM
        msg["To"] = to_email
        msg.set_content(notification.body)

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=settings.SMTP_TIMEOUT) as smtp:
            if settings.SMTP_TLS:
                smtp.starttls()
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            smtp.send_message(msg)

    @staticmethod
    def _deliver_webhook_worker(delivery_id_str: str, notification_id_str: str, url: str, secret: str) -> None:
        """Worker thread execution for webhook delivery with HMAC signature and retries."""
        import json
        db = SessionLocal()
        try:
            delivery = db.get(NotificationDelivery, UUID(delivery_id_str))
            notification = db.get(Notification, UUID(notification_id_str))
            if not delivery or not notification:
                return

            body = json.dumps({
                "notification_id": str(notification.id),
                "type": notification.type,
                "title": notification.title,
                "body": notification.body,
                "run_id": str(notification.run_id) if notification.run_id else None,
                "created_at": notification.created_at.isoformat(),
            }).encode()

            max_attempts = settings.WEBHOOK_MAX_RETRIES
            for attempt in range(1, max_attempts + 1):
                delivery.attempts = attempt
                delivery.last_attempt_at = datetime.now(UTC)

                try:
                    timestamp = str(int(time.time()))
                    signature = NotificationService._generate_webhook_signature(secret, timestamp, body)

                    headers = {
                        "Content-Type": "application/json",
                        "X-ChaosFlow-Timestamp": timestamp,
                        "X-ChaosFlow-Signature": signature,
                        "User-Agent": "ChaosFlow/0.1.0",
                    }

                    response = httpx.post(
                        url,
                        content=body,
                        headers=headers,
                        timeout=settings.WEBHOOK_TIMEOUT,
                        follow_redirects=False,
                    )
                    response.raise_for_status()

                    delivery.status = "sent"
                    delivery.last_error = None
                    db.commit()
                    break
                except Exception as exc:
                    delivery.last_error = str(exc)
                    delivery.status = "failed" if attempt == max_attempts else "pending"
                    db.commit()
                    if attempt < max_attempts:
                        time.sleep(settings.WEBHOOK_BACKOFF_BASE ** attempt)
        except Exception as exc:
            logger.warning("Webhook delivery worker exception: %s", exc)
        finally:
            db.close()

    @staticmethod
    def _deliver_email(db: Session, notification: Notification, email: str) -> None:
        """Synchronous delivery helper (useful for tests)."""
        delivery = NotificationDelivery(
            notification_id=notification.id,
            channel="email",
            target=email,
            status="pending",
            attempts=0,
        )
        db.add(delivery)
        db.commit()
        db.refresh(delivery)
        NotificationService._deliver_email_worker(str(delivery.id), str(notification.id), email)
        db.refresh(delivery)

    @staticmethod
    def _deliver_webhook(db: Session, notification: Notification, url: str, secret: str) -> None:
        """Synchronous delivery helper (useful for tests)."""
        delivery = NotificationDelivery(
            notification_id=notification.id,
            channel="webhook",
            target=url,
            status="pending",
            attempts=0,
        )
        db.add(delivery)
        db.commit()
        db.refresh(delivery)
        NotificationService._deliver_webhook_worker(str(delivery.id), str(notification.id), url, secret)
        db.refresh(delivery)

    @staticmethod
    def emit_event(db: Session, event_type: str, user_ids: list[UUID], title: str, body: str, run_id: UUID | None = None) -> list[Notification]:
        """Emit an event to multiple users (creates notifications and delivers them)."""
        notifications = []
        for user_id in user_ids:
            notification = NotificationService.create_notification(
                db, user_id, event_type, title, body, run_id
            )
            notifications.append(notification)

        # Deliver all notifications (each delivery is wrapped in try/except)
        for notification in notifications:
            try:
                NotificationService.deliver_notification(db, notification)
            except Exception as exc:
                logger.warning("Failed to deliver notification %s: %s", notification.id, exc)

        return notifications

    @staticmethod
    def mark_as_read(db: Session, user_id: UUID, notification_id: UUID) -> Notification | None:
        """Mark a notification as read."""
        notification = db.query(Notification).filter(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        ).first()
        if notification:
            notification.read = True
            db.commit()
            db.refresh(notification)
        return notification

    @staticmethod
    def mark_all_as_read(db: Session, user_id: UUID) -> int:
        """Mark all notifications as read for a user."""
        result = db.query(Notification).filter(
            Notification.user_id == user_id,
            Notification.read.is_(False),
        ).update({"read": True})
        db.commit()
        return result

    @staticmethod
    def get_unread_count(db: Session, user_id: UUID) -> int:
        """Get unread notification count for a user."""
        return db.query(Notification).filter(
            Notification.user_id == user_id,
            Notification.read.is_(False),
        ).count()

    @staticmethod
    def list_notifications(
        db: Session,
        user_id: UUID,
        page: int = 1,
        page_size: int = 20,
        unread_only: bool = False,
    ) -> tuple[list[Notification], int]:
        """List notifications for a user."""
        query = db.query(Notification).filter(Notification.user_id == user_id)
        if unread_only:
            query = query.filter(Notification.read.is_(False))
        total = query.count()
        notifications = (
            query.order_by(Notification.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return notifications, total


# Convenience functions for common events
def emit_run_failed(db: Session, pipeline_name: str, run_id: UUID, error: str, owner_id: UUID) -> None:
    """Emit run_failed event."""
    NotificationService.emit_event(
        db,
        "run_failed",
        [owner_id],
        f"Run failed: {pipeline_name}",
        f"Pipeline run failed with error: {error}",
        run_id,
    )


def emit_run_succeeded(db: Session, pipeline_name: str, run_id: UUID, owner_id: UUID) -> None:
    """Emit run_succeeded event."""
    NotificationService.emit_event(
        db,
        "run_succeeded",
        [owner_id],
        f"Run succeeded: {pipeline_name}",
        "Pipeline run completed successfully.",
        run_id,
    )


def emit_schedule_missed(db: Session, pipeline_name: str, reason: str, owner_id: UUID) -> None:
    """Emit schedule_missed event."""
    NotificationService.emit_event(
        db,
        "schedule_missed",
        [owner_id],
        f"Scheduled run missed: {pipeline_name}",
        f"Scheduled run for pipeline '{pipeline_name}' was missed: {reason}",
    )


def push_notification_to_ws(user_id: UUID, notification: Notification) -> None:
    """Push notification to WebSocket connections for a user."""
    # This will be called from the WebSocket module to avoid circular imports
    pass


def shutdown_notifications() -> None:
    """Shutdown background notification delivery pool."""
    _delivery_pool.shutdown(wait=False, cancel_futures=True)
